#!/usr/bin/env python3
"""Continuous ATS Board Harvester for search_work_world.

Automatically scrapes and ingests fresh vacancies directly from public APIs of:
- Greenhouse (https://boards-api.greenhouse.io/v1/boards/{board}/jobs)
- Ashby (https://api.ashbyhq.com/posting-api/job-board/{board})
- Lever (https://api.lever.co/v0/postings/{board}?mode=json)
- SmartRecruiters (https://api.smartrecruiters.com/v1/companies/{board}/postings)

Filters by dual-track keywords:
1. Cybersec (AppSec, DevSecOps, Cloud Security, Pentest, IAM, Vulnerability, Incident Response, etc.)
2. Creative (Product Designer, Brand Designer, Visual Designer, Art Director, Video, Lookbook, Motion, Fashion, etc.)

Loads 1,649+ company boards from bot_otclick/autopilot/config.json with ThreadPoolExecutor concurrency.
Deduplicates and inserts into jobs.db with status='queued'.
Zero cost: 100% public, CORS-enabled JSON endpoints.
"""
import os
import sys
import json
import time
import socket
import hashlib
import sqlite3
import argparse
import urllib.request
import urllib.error
import urllib.parse
import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

socket.setdefaulttimeout(10.0)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "bot_otclick/autopilot/jobs.db"
if not DB_PATH.is_file():
    DB_PATH = ROOT / "autopilot/jobs.db"

CYBERSEC_KEYWORDS = [
    "security", "appsec", "devsecops", "cloud security", "penetration", "pentest",
    "vulnerability", "infosec", "soc", "iam", "threat", "psirt", "compliance",
    "cybersecurity", "security analyst", "security engineer", "incident response",
    "identity and access", "offensive security", "product security", "detection engineer",
    "red team", "blue team", "purple team", "security architect", "security operations",
    "zero trust", "cryptography", "identity engineer", "siem", "trust & safety"
]

CREATIVE_KEYWORDS = [
    "photographer", "photography", "photo", "retoucher", "photo editor",
    "image editor", "portrait", "fashion photographer", "commercial photographer",
    "studio photographer", "videographer", "video editor", "video producer",
    "video content", "content creator", "content producer", "camera operator",
    "visual content", "creative content", "creative producer", "filmmaker",
    "colorist", "visual storyteller", "image creator", "fotograf", "videograf",
    "videoproduzent", "videoeditor", "social video", "multimedia producer"
]

GREENHOUSE_BOARDS = [
    "gitlab", "canonical", "datadog", "reddit", "stripe", "figma", "airbnb",
    "cloudflare", "palantir", "snyk", "elastic", "pinterest", "discord",
    "openai", "anthropic", "brex", "scaleapi", "ramp", "affirm", "instacart",
    "robinhood", "benchling", "chainalysis", "vanta", "dojo", "dbtlabs",
    "mongodb", "datadoghq", "hashicorp", "automattic", "verkada", "tanium",
    "alpaca", "samsara", "mendix", "checkr", "diligent", "clearbank"
]

ASHBY_BOARDS = [
    "notion", "linear", "retool", "superhuman", "cursor", "replit", "perplexity",
    "resend", "midjourney", "synthesia", "runwayml", "modal", "dub", "huggingface",
    "pigment", "zed", "anyscale", "cohere", "tldraw", "togetherai"
]

LEVER_BOARDS = [
    "atlassian", "auth0", "braze", "deliveroo", "klarna", "monzo", "zalando",
    "remote", "deel", "ledger", "kraken", "fullstory", "aircall", "ironclad"
]

SMARTRECRUITERS_BOARDS = [
    "deliveryhero", "sumup", "flohealth", "ikea", "ubisoft", "zalando", "bosch"
]

CONFIG_JSON = ROOT / "bot_otclick" / "autopilot" / "config.json"
if not CONFIG_JSON.is_file():
    CONFIG_JSON = ROOT / "autopilot" / "config.json"

if CONFIG_JSON.is_file():
    try:
        with open(CONFIG_JSON, "r", encoding="utf-8") as _cf:
            _cdata = json.load(_cf)
            _vb = _cdata.get("vendor_boards", {})
            if _vb.get("greenhouse"):
                GREENHOUSE_BOARDS = _vb["greenhouse"]
            if _vb.get("ashby"):
                ASHBY_BOARDS = _vb["ashby"]
            if _vb.get("lever"):
                LEVER_BOARDS = _vb["lever"]
            if _vb.get("smartrecruiters"):
                SMARTRECRUITERS_BOARDS = _vb["smartrecruiters"]
    except Exception:
        pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

def extract_slug(val: str) -> str:
    if not val.startswith("http"):
        return val
    parts = val.split("?")[0].strip("/").split("/")
    if "boards" in parts:
        idx = parts.index("boards")
        if idx + 1 < len(parts): return parts[idx + 1]
    if "job-board" in parts:
        idx = parts.index("job-board")
        if idx + 1 < len(parts): return parts[idx + 1]
    if "companies" in parts:
        idx = parts.index("companies")
        if idx + 1 < len(parts): return parts[idx + 1]
    if "postings" in parts:
        idx = parts.index("postings")
        if idx + 1 < len(parts):
            return parts[idx + 1]
        elif idx > 0:
            return parts[idx - 1]
    return parts[-1]

def make_job_id(company: str, title: str, url: str) -> str:
    h = hashlib.sha256(f"{company.lower().strip()}:{title.lower().strip()}:{url.strip()}".encode("utf-8")).hexdigest()
    return h[:16]

def classify_role(title: str):
    t_low = title.lower()
    is_cyber = any(k in t_low for k in CYBERSEC_KEYWORDS)
    is_creative = any(k in t_low for k in CREATIVE_KEYWORDS)
    if is_cyber and not is_creative:
        return "appsec"
    elif is_creative and not is_cyber:
        return "creative"
    elif is_cyber and is_creative:
        if any(c in t_low for c in ["designer", "art director", "video", "photo", "creative"]):
            return "creative"
        return "appsec"
    return None

def _text(raw):
    """Board HTML -> plain text with paragraph breaks. Greenhouse escapes its HTML
    once more (&lt;p&gt;), hence unescape, strip tags, unescape."""
    t = html.unescape(raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ul|ol|tr)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()

def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))

def _posting(title, url, location, description="", ident=""):
    return {"title": (title or "").strip(), "url": url, "location": location, "description": description,
            "ident": str(ident or "")}

def greenhouse_postings(board, timeout=30):
    """Every posting on the board, description included. Raises on a network/HTTP error
    (an empty list means the board is really empty) - same for the three below."""
    url = board if board.startswith("http") else f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    url = url.replace("content=false", "content=true")
    if "content=true" not in url:
        url += ("&" if "?" in url else "?") + "content=true"
    return [_posting(j.get("title"), j.get("absolute_url"), (j.get("location") or {}).get("name") or "Remote / Worldwide",
                     _text(j.get("content")), j.get("id"))
            for j in _get(url, timeout).get("jobs") or []]

def ashby_postings(board, timeout=30):
    url = board if board.startswith("http") else f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    if "includeCompensation" not in url:
        url += ("&" if "?" in url else "?") + "includeCompensation=true"
    return [_posting(j.get("title"), j.get("jobUrl"), j.get("location") or "Remote",
                     (j.get("descriptionPlain") or "").strip() or _text(j.get("descriptionHtml")), j.get("id"))
            for j in _get(url, timeout).get("jobs") or []]

def lever_postings(board, timeout=30):
    url = board if board.startswith("http") else f"https://api.lever.co/v0/postings/{board}?mode=json"
    data = _get(url, timeout)
    out = []
    for j in data if isinstance(data, list) else []:
        parts = [j.get("descriptionPlain") or _text(j.get("description"))]
        parts += [f"{l.get('text') or ''}\n{_text(l.get('content'))}" for l in j.get("lists") or []]
        parts.append(j.get("additionalPlain") or _text(j.get("additional")))
        out.append(_posting(j.get("text"), j.get("hostedUrl"), (j.get("categories") or {}).get("location") or "Remote",
                            "\n".join(p.strip() for p in parts if p and p.strip()), j.get("id")))
    return out

def smartrecruiters_postings(board, timeout=30):
    """The list API carries no description; smartrecruiters_description() fetches one."""
    url = board if board.startswith("http") else f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    out = []
    for j in _get(url, timeout).get("content") or []:
        loc_obj = j.get("location") or {}
        loc = ", ".join(p for p in (loc_obj.get("city"), loc_obj.get("country")) if p) or "Remote"
        j_url = ((j.get("actions") or {}).get("public") or {}).get("jobAd") or \
            f"https://jobs.smartrecruiters.com/{extract_slug(board)}/{j.get('id')}"
        out.append(_posting(j.get("name"), j_url, loc))
    return out

def smartrecruiters_description(url, timeout=20, strict=False):
    """Description of one jobs.smartrecruiters.com/<company>/<id> posting, '' if unknown.
    strict=True: None when the posting is gone (HTTP 404); other errors raise."""
    m = re.search(r"smartrecruiters\.com/([^/?#]+)/(\d+)", url or "")
    if not m:
        return ""
    try:
        sections = (_get(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}", timeout)
                    .get("jobAd") or {}).get("sections") or {}
    except Exception as e:
        if not strict:
            return ""
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            return None
        raise
    keys = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    return "\n".join(t for t in (_text((sections.get(k) or {}).get("text")) for k in keys) if t)

BOARD_POSTINGS = {"greenhouse": greenhouse_postings, "ashby": ashby_postings,
                  "lever": lever_postings, "smartrecruiters": smartrecruiters_postings}

def _fetch_board(src, board):
    """Role-matching postings of one board for harvest_all; [] on any error."""
    company = extract_slug(board).capitalize()
    try:
        postings = BOARD_POSTINGS[src](board)
    except Exception:
        return []
    out = []
    for p in postings:
        persona = classify_role(p["title"])
        if persona and p["title"] and p["url"]:
            out.append({**p, "source": src, "company": company, "persona": persona})
    return out

def fetch_greenhouse(board: str):
    return _fetch_board("greenhouse", board)

def fetch_ashby(board: str):
    return _fetch_board("ashby", board)

def fetch_lever(board: str):
    return _fetch_board("lever", board)

def fetch_smartrecruiters(board: str):
    return _fetch_board("smartrecruiters", board)

def fetch_wwr_jobs():
    import xml.etree.ElementTree as ET
    feeds = [
        ("https://weworkremotely.com/categories/remote-design-jobs.rss", "creative"),
        ("https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss", "appsec")
    ]
    results = []
    for feed_url, fallback_persona in feeds:
        try:
            req = urllib.request.Request(feed_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read())
                for item in root.findall(".//item"):
                    full_title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    if not link or not full_title:
                        continue
                    if ":" in full_title:
                        comp_raw, job_title = full_title.split(":", 1)
                        company = comp_raw.strip()
                        title = job_title.strip()
                    else:
                        company = "Remote Startup"
                        title = full_title
                    persona = classify_role(title) or fallback_persona
                    results.append({
                        "source": "wwr",
                        "company": company,
                        "title": title,
                        "location": "Remote / Worldwide",
                        "url": link,
                        "persona": persona,
                        "description": _text(item.findtext("description")),
                    })
        except Exception:
            pass
    return results

def fetch_remoteok_jobs():
    tags = [("design", "creative"), ("security", "appsec")]
    results = []
    for tag, fallback_persona in tags:
        try:
            url = f"https://remoteok.com/api?tag={tag}"
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                if isinstance(data, list):
                    for j in data:
                        if not isinstance(j, dict) or not j.get("id"):
                            continue
                        title = (j.get("position") or "").strip()
                        comp = (j.get("company") or "Remote Startup").strip()
                        j_url = j.get("url") or f"https://remoteok.com/l/{j.get('id')}"
                        loc = (j.get("location") or "Remote").strip()
                        if not title or not j_url:
                            continue
                        persona = classify_role(title) or fallback_persona
                        results.append({
                            "source": "remoteok",
                            "company": comp,
                            "title": title,
                            "location": loc,
                            "url": j_url,
                            "persona": persona,
                            "description": _text(j.get("description")),
                        })
        except Exception:
            pass
    return results

def fetch_yc_startups_jobs(limit: int = 150):
    try:
        url = "https://yc-oss.github.io/api/companies/hiring.json"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            companies = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return []

    active = [c for c in companies if c.get("status") == "Active" and c.get("slug")]
    if limit:
        active = active[:limit]

    results = []
    def probe_yc(c):
        slug = c.get("slug", "").strip().lower()
        jobs = fetch_greenhouse(slug)
        if jobs: return jobs
        jobs = fetch_ashby(slug)
        if jobs: return jobs
        jobs = fetch_lever(slug)
        if jobs: return jobs
        return []

    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = [ex.submit(probe_yc, c) for c in active]
        for f in as_completed(futures):
            try:
                res = f.result()
                if res:
                    for j in res:
                        j["source"] = "yc_startups"
                    results.extend(res)
            except Exception:
                pass
    return results

def fetch_jobicy_jobs():
    categories = [
        ("design-multimedia", "creative"),
        ("engineering", "appsec")
    ]
    results = []
    for cat, fallback_persona in categories:
        try:
            url = f"https://jobicy.com/api/v2/remote-jobs?count=50&industry={cat}"
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                jobs = data.get("jobs", [])
                for j in jobs:
                    title = (j.get("jobTitle") or "").strip()
                    comp = (j.get("companyName") or "Remote Company").strip()
                    j_url = j.get("url")
                    loc = (j.get("jobGeo") or "Remote").strip()
                    if not title or not j_url:
                        continue
                    persona = classify_role(title) or (fallback_persona if cat == "design-multimedia" else None)
                    if not persona:
                        continue
                    results.append({
                        "source": "jobicy",
                        "company": comp,
                        "title": title,
                        "location": loc,
                        "url": j_url,
                        "persona": persona,
                        "description": _text(j.get("jobDescription") or j.get("jobExcerpt")),
                    })
        except Exception:
            pass
    return results

def fetch_arbeitnow_jobs():
    results = []
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            jobs = data.get("data", [])
            for j in jobs:
                title = (j.get("title") or "").strip()
                comp = (j.get("company_name") or "Remote Company").strip()
                j_url = j.get("url")
                loc = (j.get("location") or "Europe / Remote").strip()
                if not title or not j_url:
                    continue
                persona = classify_role(title)
                if not persona:
                    continue
                results.append({
                    "source": "arbeitnow",
                    "company": comp,
                    "title": title,
                    "location": loc,
                    "url": j_url,
                    "persona": persona,
                    "description": _text(j.get("description")),
                })
    except Exception:
        pass
    return results

def fetch_himalayas_jobs():
    results = []
    try:
        url = "https://himalayas.app/jobs/api?limit=60"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            jobs = data.get("jobs", [])
            for j in jobs:
                title = (j.get("title") or "").strip()
                comp = (j.get("companyName") or "Remote Company").strip()
                j_url = j.get("applicationUrl") or (f"https://himalayas.app/companies/{j.get('companySlug')}/jobs/{j.get('slug')}" if j.get("slug") else None)
                loc = "Remote / Worldwide"
                if not title or not j_url:
                    continue
                persona = classify_role(title)
                if not persona:
                    continue
                results.append({
                    "source": "himalayas",
                    "company": comp,
                    "title": title,
                    "location": loc,
                    "url": j_url,
                    "persona": persona,
                    "description": _text(j.get("description") or j.get("excerpt")),
                })
    except Exception:
        pass
    return results

def fetch_hacker_news_jobs():
    results = []
    try:
        req = urllib.request.Request(
            "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=Ask+HN:+Who+is+hiring&hitsPerPage=2",
            headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            stories = [h["objectID"] for h in data.get("hits", []) if "Who is hiring" in h.get("title", "")]

        for sid in stories[:2]:
            req_c = urllib.request.Request(
                f"https://hn.algolia.com/api/v1/search?tags=comment,story_{sid}&hitsPerPage=500",
                headers=HEADERS
            )
            with urllib.request.urlopen(req_c, timeout=12) as c_resp:
                c_data = json.loads(c_resp.read().decode("utf-8", "ignore"))
                for c in c_data.get("hits", []):
                    if c.get("parent_id") != int(sid):
                        continue
                    raw = html.unescape(c.get("comment_text") or "")
                    text = re.sub(r"<[^>]+>", " ", raw)
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    if not lines:
                        continue
                    first = lines[0]
                    first_low = first.lower()
                    if first_low.startswith("location:") or first_low.startswith("seeking work") or first_low.startswith("freelancer") or "seeking work" in first_low:
                        continue
                    parts = [p.strip() for p in first.split("|")]
                    comp = parts[0] if parts else "Startup"
                    if comp.lower().startswith("location:"):
                        continue
                    if "(" in comp:
                        comp = comp.split("(")[0].strip()
                    if len(comp) > 40:
                        comp = comp[:40].strip()

                    low = text.lower()
                    c_hits = any(k in low for k in CREATIVE_KEYWORDS)
                    s_hits = any(k in low for k in CYBERSEC_KEYWORDS)
                    if c_hits and not s_hits:
                        persona = "creative"
                    elif s_hits and not c_hits:
                        persona = "appsec"
                    elif c_hits and s_hits:
                        persona = "creative" if any(k in low for k in ["designer", "art director", "video", "photo", "creative"]) else "appsec"
                    else:
                        continue

                    urls = re.findall(r"https?://[^\s<>\"')]+", raw)
                    external_urls = [u for u in urls if "ycombinator.com" not in u]
                    j_url = external_urls[0] if external_urls else f"https://news.ycombinator.com/item?id={c.get('objectID')}"

                    title = parts[1] if len(parts) > 1 and len(parts[1]) < 60 else ("Product Designer" if persona == "creative" else "Security Engineer")
                    loc = parts[2] if len(parts) > 2 and len(parts[2]) < 60 else "Remote / Worldwide"

                    results.append({
                        "source": "hackernews",
                        "company": comp,
                        "title": title,
                        "location": loc,
                        "url": j_url,
                        "persona": persona,
                        "description": _text(raw),
                    })
    except Exception:
        pass
    return results

def fetch_cryptocurrency_jobs():
    results = []
    try:
        url = "https://cryptocurrencyjobs.co/index.xml"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
            items = root.findall(".//item")
            for it in items:
                raw_title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if not raw_title or not link:
                    continue
                if " at " in raw_title:
                    title, comp = raw_title.rsplit(" at ", 1)
                else:
                    title, comp = raw_title, "Web3 Startup"
                persona = classify_role(title)
                if not persona:
                    continue
                results.append({
                    "source": "cryptocurrencyjobs",
                    "company": comp.strip(),
                    "title": title.strip(),
                    "location": "Remote / Worldwide",
                    "url": link,
                    "persona": persona,
                    "description": _text(it.findtext("description")),
                })
    except Exception:
        pass
    return results

def harvest_all(limit_boards: int = 0, dry_run: bool = False, max_workers: int = 20, source_filter: str = "all"):
    print("============================================================")
    print("         CONTINUOUS ATS PUBLIC HARVESTER                    ")
    print(f"         Target DB: {DB_PATH}")
    print(f"         Concurrency: {max_workers} threads | Sources: {source_filter}")
    print("============================================================")

    if not DB_PATH.is_file():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    existing_urls = set(r[0] for r in cur.execute("SELECT url FROM jobs WHERE url IS NOT NULL").fetchall())
    existing_pairs = set((r[0].lower().strip(), r[1].lower().strip()) for r in cur.execute("SELECT company, title FROM jobs").fetchall())

    print(f"Loaded {len(existing_urls)} existing URLs and {len(existing_pairs)} company-title pairs from jobs.db.")

    total_harvested = []
    creative_found = 0
    cyber_found = 0

    # 1. Direct Feeds (WWR & RemoteOK)
    if source_filter in ("all", "direct_feeds", "direct"):
        print("\n--- Harvesting direct high-signal feeds (WWR & RemoteOK) ---")
        try:
            wwr_jobs = fetch_wwr_jobs()
            print(f"  * WWR RSS Feeds: +{len(wwr_jobs)} matching jobs")
            total_harvested.extend(wwr_jobs)
        except Exception as e:
            print(f"  * WWR error: {e}")

        try:
            rok_jobs = fetch_remoteok_jobs()
            print(f"  * RemoteOK APIs: +{len(rok_jobs)} matching jobs")
            total_harvested.extend(rok_jobs)
        except Exception as e:
            print(f"  * RemoteOK error: {e}")

    # 2. Direct Public Remote APIs (Jobicy, Arbeitnow, Himalayas)
    if source_filter in ("all", "api_feeds", "direct_feeds"):
        print("\n--- Harvesting direct public remote APIs (Jobicy, Arbeitnow, Himalayas) ---")
        try:
            jobicy_jobs = fetch_jobicy_jobs()
            print(f"  * Jobicy API: +{len(jobicy_jobs)} matching jobs")
            total_harvested.extend(jobicy_jobs)
        except Exception as e:
            print(f"  * Jobicy error: {e}")

        try:
            arbeit_jobs = fetch_arbeitnow_jobs()
            print(f"  * Arbeitnow API: +{len(arbeit_jobs)} matching jobs")
            total_harvested.extend(arbeit_jobs)
        except Exception as e:
            print(f"  * Arbeitnow error: {e}")

        try:
            himalayas_jobs = fetch_himalayas_jobs()
            print(f"  * Himalayas API: +{len(himalayas_jobs)} matching jobs")
            total_harvested.extend(himalayas_jobs)
        except Exception as e:
            print(f"  * Himalayas error: {e}")

    # 3. Hidden Startup & Community Feeds (Hacker News & CryptoJobs)
    if source_filter in ("all", "hidden", "hn", "crypto", "api_feeds"):
        print("\n--- Harvesting hidden startup & community threads (Hacker News & CryptoJobs) ---")
        try:
            hn_jobs = fetch_hacker_news_jobs()
            print(f"  * Hacker News Who Is Hiring: +{len(hn_jobs)} matching jobs")
            total_harvested.extend(hn_jobs)
        except Exception as e:
            print(f"  * Hacker News error: {e}")

        try:
            crypto_jobs = fetch_cryptocurrency_jobs()
            print(f"  * CryptocurrencyJobs: +{len(crypto_jobs)} matching jobs")
            total_harvested.extend(crypto_jobs)
        except Exception as e:
            print(f"  * CryptocurrencyJobs error: {e}")

    # 4. YC 1,495 Hiring Startups
    if source_filter in ("all", "yc", "startups"):
        print("\n--- Harvesting Y Combinator hiring startups ---")
        try:
            yc_limit = limit_boards * 5 if limit_boards else 100
            yc_jobs = fetch_yc_startups_jobs(limit=yc_limit)
            print(f"  * YC Startups Probe: +{len(yc_jobs)} matching jobs")
            total_harvested.extend(yc_jobs)
        except Exception as e:
            print(f"  * YC Startups error: {e}")

    # 3. Direct ATS Company Boards (Greenhouse, Ashby, Lever, SmartRecruiters)
    if source_filter in ("all", "ats"):
        gh_list = GREENHOUSE_BOARDS[:limit_boards] if limit_boards else GREENHOUSE_BOARDS
        ashby_list = ASHBY_BOARDS[:limit_boards] if limit_boards else ASHBY_BOARDS
        lever_list = LEVER_BOARDS[:limit_boards] if limit_boards else LEVER_BOARDS
        sr_list = SMARTRECRUITERS_BOARDS[:limit_boards] if limit_boards else SMARTRECRUITERS_BOARDS

        tasks = []
        for b in gh_list:
            tasks.append(("greenhouse", b, fetch_greenhouse))
        for b in ashby_list:
            tasks.append(("ashby", b, fetch_ashby))
        for b in lever_list:
            tasks.append(("lever", b, fetch_lever))
        for b in sr_list:
            tasks.append(("smartrecruiters", b, fetch_smartrecruiters))

        print(f"\n--- Harvesting {len(tasks)} direct ATS company boards ---")
        print(f"  * Greenhouse: {len(gh_list)} boards")
        print(f"  * Ashby:      {len(ashby_list)} boards")
        print(f"  * Lever:      {len(lever_list)} boards")
        print(f"  * SmartRecr:  {len(sr_list)} boards")

        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_board = {executor.submit(func, board): (src, board) for src, board, func in tasks}
            for future in as_completed(future_to_board):
                src, board = future_to_board[future]
                completed_count += 1
                try:
                    jobs = future.result()
                    if jobs:
                        total_harvested.extend(jobs)
                        company_name = extract_slug(board).capitalize()
                        c_curr = sum(1 for j in jobs if j["persona"] == "creative")
                        s_curr = sum(1 for j in jobs if j["persona"] == "appsec")
                        print(f"  [{completed_count:>4}/{len(tasks)}] [{src:<15}] {company_name:<20}: +{len(jobs)} jobs (C:{c_curr} S:{s_curr})")
                except Exception:
                    pass

    creative_found = sum(1 for j in total_harvested if j["persona"] == "creative")
    cyber_found = sum(1 for j in total_harvested if j["persona"] == "appsec")
    print(f"\nHarvesting complete. Discovered: {len(total_harvested)} matching vacancies (Creative: {creative_found}, Cybersec: {cyber_found}).")

    # SmartRecruiters lists postings without text: one detail call per NEW posting
    sr_new = [j for j in total_harvested if j["source"] == "smartrecruiters"
              and not j.get("description") and j["url"] not in existing_urls]
    if sr_new and not dry_run:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for j, d in zip(sr_new, ex.map(lambda j: smartrecruiters_description(j["url"]), sr_new)):
                j["description"] = d

    new_jobs = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for j in total_harvested:
        url = j["url"]
        comp = j["company"]
        title = j["title"]

        if url in existing_urls:
            continue
        if (comp.lower().strip(), title.lower().strip()) in existing_pairs:
            continue

        j_id = make_job_id(comp, title, url)
        cv_file = "creative.pdf" if j["persona"] == "creative" else "appsec.pdf"
        score = 85 if j["persona"] == "creative" else 90

        new_jobs.append((
            j_id,
            j["source"],
            j["persona"],
            title,
            comp,
            j["location"],
            url,
            j.get("description") or f"Auto-harvested via {j['source']} board public API.",
            score,
            cv_file,
            "queued",
            now_iso,
            now_iso,
            0,
            0,
            None,
            None,
            "Competitive",
            j["persona"]
        ))
        existing_urls.add(url)
        existing_pairs.add((comp.lower().strip(), title.lower().strip()))

    new_creative = sum(1 for j in new_jobs if j[2] == "creative")
    new_cyber = sum(1 for j in new_jobs if j[2] == "appsec")
    print(f"Fresh deduplicated vacancies ready for insertion: {len(new_jobs)} (Creative: {new_creative}, Cybersec: {new_cyber})")

    if not dry_run and new_jobs:
        cur.executemany("""
            INSERT OR IGNORE INTO jobs (
                id, source, keyword, title, company, location, url,
                description, score, cv, status, created_at, updated_at,
                dead_listing, hidden, claim_token, claimed_at, salary, persona
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, new_jobs)
        con.commit()
        print(f"Successfully inserted {len(new_jobs)} fresh vacancies into jobs.db with status='queued'!")
    elif dry_run:
        print("DRY-RUN mode: No database records were modified.")

    con.close()

PLACEHOLDER_LIKE = "Auto-harvested via %"
SPONSOR_PLACEHOLDER_LIKE = "% register of % (board %/%)."   # tools/sponsors.py discover_postings
MISS_CACHE = DB_PATH.parent / "desc_backfill_misses.json"
MISS_TTL_HOURS = 24

def board_of(row):
    """(ats, board slug) a harvested row's posting lives on, else None."""
    from ats_schema import _route
    u = urllib.parse.urlparse(row.get("url") or "")
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    route = _route(row.get("url"))
    if route and route[0] in ("greenhouse", "ashby"):
        return route[0], route[1]
    if host.endswith("greenhouse.io") and parts and parts[0] != "embed":
        return "greenhouse", parts[0]
    if host == "jobs.lever.co" and parts:
        return "lever", parts[0]
    if host == "jobs.smartrecruiters.com" and parts:
        return "smartrecruiters", parts[0]
    if "gh_jid=" in u.query:   # custom careers domain: the board is named after the company
        return "greenhouse", (row.get("company") or "").lower()
    if row.get("source") in BOARD_POSTINGS and row.get("company"):
        return row["source"], row["company"].lower()
    return None

def _refresh_board(ats, slug, rows):
    """One board call for all its rows -> ({id: description}, [dead ids]); raises when the
    board itself can't be read, so a network blip never marks anything dead."""
    found, dead = {}, []
    if ats == "smartrecruiters":   # the list API is paginated, so ask for each posting itself
        for r in rows:
            d = smartrecruiters_description(r["url"], strict=True)
            if d is None:
                dead.append(r["id"])
            elif d:
                found[r["id"]] = d
        return found, dead
    postings = BOARD_POSTINGS[ats](slug)
    by_url = {p["url"]: p for p in postings if p["url"]}
    by_id = {p["ident"]: p for p in postings if p["ident"]}
    by_title = {p["title"].lower(): p for p in postings if p["title"]}
    for r in rows:
        m = re.search(r"(?:gh_jid=|/jobs/|/)(\d{5,}|[0-9a-f]{8}-[0-9a-f-]{27})", r["url"] or "")
        p = by_url.get(r["url"]) or (m and by_id.get(m[1])) or by_title.get((r["title"] or "").strip().lower())
        if not p:
            dead.append(r["id"])
        elif p["description"]:
            found[r["id"]] = p["description"]
    return found, dead

def backfill_descriptions(limit=0, days=7, workers=12, dry_run=False):
    """Replace "Auto-harvested via ..." placeholders with the posting text for recent
    queued title-fit rows, one API call per board; postings gone from their board
    become dead_listing=1. Boards that failed are skipped for MISS_TTL_HOURS."""
    import autopilot as A
    cfg = A.read_json(A.CFG)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:19]
    rows = [dict(r) for r in con.execute(
        "SELECT id, url, title, company, source, persona FROM jobs WHERE (description LIKE ? OR description LIKE ? OR length(coalesce(description, '')) < 200) "
        "AND status='queued' AND dead_listing=0 AND hidden=0 AND created_at >= ? ORDER BY created_at DESC",
        (PLACEHOLDER_LIKE, SPONSOR_PLACEHOLDER_LIKE, since))]
    rows = [r for r in rows if A.title_fit(r, cfg)]
    if limit:
        rows = rows[:limit]
    try:
        misses = json.loads(MISS_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        misses = {}
    now = datetime.now(timezone.utc)
    fresh_miss = {k for k, ts in misses.items()
                  if now - datetime.fromisoformat(ts) < timedelta(hours=MISS_TTL_HOURS)}
    boards, no_board = {}, 0
    for r in rows:
        b = board_of(r)
        if b is None:
            no_board += 1
        elif f"{b[0]}:{b[1]}" not in fresh_miss:
            boards.setdefault(b, []).append(r)
    print(f"Backfill: {len(rows)} title-fit placeholder rows (last {days}d) on {len(boards)} boards; "
          f"{no_board} rows have no board API, {len(fresh_miss)} boards skipped as recent misses")
    got, dead, failed, failed_rows = {}, [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_refresh_board, ats, slug, rs): (ats, slug) for (ats, slug), rs in boards.items()}
        for f in as_completed(futs):
            ats, slug = futs[f]
            try:
                found, gone = f.result()
            except Exception as e:
                failed.append(f"{ats}:{slug}")
                failed_rows += len(boards[(ats, slug)])
                print(f"  [miss] {ats}:{slug}: {type(e).__name__}: {str(e)[:80]}")
                continue
            got.update(found)
            dead += gone
    now_iso = now.isoformat()
    if not dry_run:
        con.executemany("UPDATE jobs SET description=?, updated_at=? WHERE id=?",
                        [(d, now_iso, jid) for jid, d in got.items()])
        con.executemany("UPDATE jobs SET dead_listing=1, updated_at=? WHERE id=?", [(now_iso, jid) for jid in dead])
        con.commit()
        misses = {k: ts for k, ts in misses.items() if k in fresh_miss}
        misses.update({k: now_iso for k in failed})
        MISS_CACHE.write_text(json.dumps(misses, indent=1), encoding="utf-8")
    con.close()
    result = {"rows": len(rows), "boards": len(boards), "described": len(got), "dead": len(dead),
              "failed_boards": len(failed), "failed_board_rows": failed_rows, "no_board_api": no_board, "dry_run": dry_run}
    print(json.dumps(result))
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous ATS Board Harvester")
    parser.add_argument("--limit-boards", type=int, default=0, help="Limit number of boards to query per ATS (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without inserting into jobs.db")
    parser.add_argument("--workers", type=int, default=20, help="Thread pool concurrency")
    parser.add_argument("--source", type=str, default="all", choices=["all", "ats", "direct_feeds", "api_feeds", "yc", "hidden", "hn", "crypto"], help="Source filter")
    parser.add_argument("--backfill-descriptions", action="store_true",
                        help="Fill placeholder descriptions of recent title-fit queued jobs from their boards")
    parser.add_argument("--limit", type=int, default=0, help="Backfill: max rows (newest first)")
    parser.add_argument("--days", type=float, default=7, help="Backfill: only rows created in the last D days")
    args = parser.parse_args()

    if args.backfill_descriptions:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        backfill_descriptions(limit=args.limit, days=args.days, workers=min(args.workers, 12), dry_run=args.dry_run)
        sys.exit(0)
    harvest_all(limit_boards=args.limit_boards, dry_run=args.dry_run, max_workers=args.workers, source_filter=args.source)
