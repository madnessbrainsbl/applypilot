"""Vacancy discovery techniques taken from the neighbouring FindAll project (read-only), as permanent sources.

Two `discover --source` names, public data only, no login, no captcha / anti-bot handling:

  findall_import  FindAll's own stores, read-only: jobs table of jobfind.db and the jobs_*/search_jobs_* CSV reports
                  under jobfind_out. Postings that are still fresh are passed on; every ATS board named by an old
                  URL (greenhouse / lever / ashby / ...) is re-read live, so closed roles drop out and real
                  descriptions come from the board API.
  findall_hosts   careers.* / jobs.* / karriere.* hosts found WITHOUT a search engine: certificate-transparency
                  names (crt.sh, certspotter fallback) and the Wayback Machine CDX index for each target company
                  domain (FindAll domain_recon.py / archives.py). Each entry point is then read like a careers page:
                  schema.org JobPosting JSON-LD, embedded ATS boards, Workday CXS portals, persona-fit detail links.
                  Tracks postings over time (first_seen, last_new) and re-reads active hosts sooner than quiet ones.

Target domains: security seeds (companies whose board security_osint could not resolve go first), any
autopilot/seeds/findall_domains.json ([{domain,name}] or ["a.com"]), jsonld-style seed files ([{site,name}]) and
the employer hosts already in jobs.db. Store: autopilot/cache/findall_osint.json.

Config (autopilot/config.json): discovery.findall_osint = {enabled, import: {...}, hosts: {...}} (DEFAULTS below).
CLI: python tools/findall_osint.py stats | domains | run [--domains N]   (the real run is `discover --source findall_hosts`)
"""
import csv
import glob
import html
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jsonld_careers as J

ROOT = Path(__file__).resolve().parents[1]
CACHE, SEEDS = ROOT / "autopilot/cache", ROOT / "autopilot/seeds"
STORE = CACHE / "findall_osint.json"
SOURCES = ("findall_import", "findall_hosts")
SUMMARY = {}
UA = "Mozilla/5.0 (compatible; JobSearchBot/1.0; personal job search; public pages only)"
DEFAULTS = {
    "enabled": True,
    "import": {"enabled": False, "root": "",
               "max_age_days": 21, "boards_per_run": 80, "board_recheck_days": 3, "workers": 8, "max_items": 3000},
    "hosts": {"enabled": True, "domains_per_run": 60, "workers": 16, "ct": True, "wayback": True,
              "domain_recheck_days": 60, "recheck_days": 5, "quiet_recheck_days": 21, "dead_recheck_days": 45,
              "max_entries_per_domain": 3, "detail_cap": 4, "workday_cap": 12, "max_age_days": 14,
              "budget_s": 1500, "ct_gap_s": 3.0, "cdx_gap_s": 1.5, "max_items": 3000},
}
_REMOTE = re.compile(r"remote|worldwide|anywhere|hybrid", re.I)

# ---------------------------------------------------------------- polite HTTP + per-host circuit breaker
_LOCK = threading.Lock()
_ROBOTS = {}
_NEXT, _FAILS, _OPEN_UNTIL = {}, {}, {}


def _http(url, gap, timeout=40, max_bytes=8_000_000):
    """text or None. One request per `gap` s per host; 3 failures in a row open the host's breaker for 10 min
    (FindAll resilience.CircuitBreaker, trimmed); 429/503 open it at once."""
    host = urllib.parse.urlparse(url).netloc.lower()
    with _LOCK:
        if _OPEN_UNTIL.get(host, 0) > time.monotonic():
            return None
        at = max(time.monotonic(), _NEXT.get(host, 0.0))
        _NEXT[host] = at + gap
    if at > time.monotonic():
        time.sleep(at - time.monotonic())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes).decode("utf-8", "ignore")
        _FAILS[host] = 0
        return data
    except urllib.error.HTTPError as e:
        with _LOCK:
            if e.code in (429, 503):
                _OPEN_UNTIL[host] = time.monotonic() + 600
            else:
                _FAILS[host] = _FAILS.get(host, 0) + 1
    except Exception:
        with _LOCK:
            _FAILS[host] = _FAILS.get(host, 0) + 1
    with _LOCK:
        if _FAILS.get(host, 0) >= 3:
            _OPEN_UNTIL[host], _FAILS[host] = time.monotonic() + 600, 0
    return None


# ---------------------------------------------------------------- certificate transparency + Wayback (pure parsers)
CAREER_LABELS = {"careers", "career", "jobs", "job", "karriere", "karrieren", "stellen", "stellenangebote", "recruiting",
                 "recruitment", "recruit", "join", "joinus", "hiring", "apply", "talent", "werken", "vacatures", "vacancies",
                 "emploi", "emplois", "carrieres", "recrutement", "lavora", "lavoraconnoi", "empleo", "trabaja", "trabajo",
                 "jobsearch", "opportunities", "kariera", "praca", "rekrytering", "bewerbung", "jobboerse", "work"}
NOISE_LABELS = {"staging", "stage", "stg", "dev", "test", "qa", "uat", "preprod", "admin", "internal", "sandbox", "api",
                "mail", "email", "vpn", "sso", "login", "auth", "cdn", "static", "origin", "edge", "autodiscover"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def ct_names(text):
    """Host names from a crt.sh JSON body (works on a truncated body) or a certspotter list."""
    names = set()
    if text and text.lstrip().startswith("[") and '"dns_names"' in text:
        try:
            for row in json.loads(text):
                names.update(row.get("dns_names") or [])
        except ValueError:
            pass
    for blob in re.findall(r'"name_value":"([^"]*)"', text or ""):
        names.update(blob.split("\\n"))
    return {n.strip().lower().lstrip("*.").rstrip(".") for n in names if n.strip()}


def career_hosts(names, domain):
    """Names under `domain` whose labels say careers / jobs / karriere (staging & co removed), shortest first."""
    out = []
    for n in names:
        if not (n.endswith("." + domain) and DOMAIN_RE.match(n)):
            continue
        labels = set(re.split(r"[.\-_]", n[: -len(domain) - 1]))
        if labels & CAREER_LABELS and not labels & NOISE_LABELS:
            out.append(n)
    return sorted(out, key=lambda n: (len(n), n))


_CDX_PATH = re.compile(r"(?:career|karriere|/jobs?(?:/|$)|stellen|vacanc|emploi|recrut|vacature|werken|lavora|join-us|"
                       r"work-with-us|openings|opportunit)", re.I)
_CDX_SKIP_HOST = {"blog", "news", "press", "investors", "ir", "support", "help", "docs", "community", "shop", "store", "learn", "academy"}
_CDX_NOT = re.compile(r"blog|news|press|event|podcast|login|signin|/legal|privacy|\.(?:pdf|jpe?g|png|gif|svg|css|js|xml|txt)$", re.I)


def cdx_entries(rows, domain):
    """Wayback CDX 'original' URLs -> up to 4 listing-page URLs on the company site (https, no query, shallow)."""
    seen, picked = set(), []
    for u in rows:
        try:
            p = urllib.parse.urlsplit(u if "//" in u else "http://" + u)
            host = (p.hostname or "").lower()
        except ValueError:
            continue
        path = re.sub(r"/index\.(?:html?|php)$", "", p.path).rstrip("/") or "/"
        if not (host == domain or host.endswith("." + domain)) or path.count("/") > 3 or not (_CDX_PATH.search(host + path) or career_hosts({host}, domain)):
            continue
        if _CDX_NOT.search(path) or "wp-content" in path:
            continue
        first = host.split(".")[0] if host != domain else ""
        if first in _CDX_SKIP_HOST or re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", first):   # blog.x, br.x: locale/section copies
            continue
        key = (host if career_hosts({host}, domain) else "", path.lower())
        if key not in seen:
            seen.add(key)
            picked.append((len(path), f"https://{host}{path if path != '/' else ''}"))
    return [u for _, u in sorted(picked)][:4]


def fetch_ct(domain, gap):
    q = urllib.parse.quote(f"%.{domain}")
    names = ct_names(_http(f"https://crt.sh/?q={q}&output=json&exclude=expired", gap, timeout=45))
    if not names:   # crt.sh often answers 502 under load: certspotter is the fallback FindAll uses as well
        names = ct_names(_http("https://api.certspotter.com/v1/issuances?" + urllib.parse.urlencode(
            {"domain": domain, "include_subdomains": "true", "expand": "dns_names"}), gap, timeout=30, max_bytes=3_000_000))
    return names


def fetch_cdx(domain, gap):
    flt = "original:.*(career|karriere|/jobs|stellen|vacanc|emploi|recrut|vacature|werken|lavora|join-us).*"
    url = ("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        [("url", domain), ("matchType", "domain"), ("fl", "original"), ("output", "json"), ("collapse", "urlkey"),
         ("filter", "statuscode:200"), ("filter", flt), ("limit", "400")]))
    text = _http(url, gap, timeout=40, max_bytes=2_000_000)
    try:
        rows = json.loads(text) if text else None
    except ValueError:
        rows = None
    return None if rows is None else [r[0] for r in rows[1:] if isinstance(r, list) and r]


# ---------------------------------------------------------------- Workday CXS (the one portal type jsonld_careers does not read)
_WORKDAY = re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)
_WD_SKIP = {"wday", "login", "assets", "en-us", "authgwy"}


def find_workday(text):
    out = []
    for t, wd, site in _WORKDAY.findall(text or ""):
        key = (t.lower(), wd.lower(), site)
        if site.lower() not in _WD_SKIP and key not in out:
            out.append(key)
    return out


def workday_rows(tenant, wd, site, keywords, classify, cap):
    import workday_cxs_scanner as W
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    rows, seen = [], set()
    for kw in keywords:
        for it in W.fetch_workday_cxs_jobs(tenant, wd, site, limit=20, search_text=kw):
            path, title = it.get("externalPath") or "", (it.get("title") or "").strip()
            if not path or path in seen or not classify(title):
                continue
            seen.add(path)
            info = {}
            raw = J.get(f"{base}/wday/cxs/{tenant}/{site}{path}", 12)
            try:
                info = (json.loads(raw[0]) if raw else {}).get("jobPostingInfo") or {}
            except ValueError:
                pass
            rows.append({"title": title, "company": tenant.replace("-", " ").title(), "url": f"{base}/en-US/{site}{path}",
                         "location": it.get("locationsText") or info.get("location") or "",
                         "posted": str(info.get("startDate") or "")[:10], "valid_through": "",
                         "description": J._text(info.get("jobDescription") or "")[:6000], "src": f"workday:{tenant}"})
            if len(rows) >= cap:
                return rows
    return rows


# ---------------------------------------------------------------- ATS boards jsonld_careers does not know (public JSON APIs)
_EXTRA_ATS = [("rippling", re.compile(r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs", re.I)),
              ("gem", re.compile(r"jobs\.gem\.com/([\w-]+)", re.I))]


def find_extra_ats(text):
    return list(dict.fromkeys((k, m.lower()) for k, rx in _EXTRA_ATS for m in rx.findall(text or "")))


def extra_ats_rows(kind, slug, classify, cap=12):
    """Rippling / Gem board -> persona-fit postings with descriptions (same row shape as jsonld_careers._ats_rows)."""
    rows = []
    if kind == "gem":
        for j in json.loads((J.get(f"https://api.gem.com/job_board/v0/{slug}/job_posts/", 20) or ("[]",))[0]):
            if classify(j.get("title") or ""):
                rows.append({"title": j["title"], "url": j.get("absolute_url"), "location": (j.get("location") or {}).get("name") or "",
                             "description": j.get("content_plain") or J._text(j.get("content") or ""),
                             "posted": str(j.get("first_published_at") or "")[:10], "src": f"ats:gem:{slug}"})
    elif kind == "rippling":
        api = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
        for j in json.loads((J.get(api, 20) or ("[]",))[0]):
            title = (j.get("name") or "").strip()
            if not classify(title) or len(rows) >= cap:
                continue
            d = json.loads((J.get(f"{api}/{j['uuid']}", 20) or ("{}",))[0])
            desc = d.get("description") if isinstance(d.get("description"), dict) else {}
            rows.append({"title": title, "url": j.get("url"), "location": ", ".join(d.get("workLocations") or []) or (j.get("workLocation") or {}).get("label", ""),
                         "description": J._text((desc.get("company") or "") + (desc.get("role") or "")),
                         "posted": str(d.get("createdOn") or "")[:10], "company": d.get("companyName") or "", "src": f"ats:rippling:{slug}"})
    return [r for r in rows if r.get("url")]


# ---------------------------------------------------------------- reading one entry point
def read_entry(url, classify, keywords, o, t_end):
    """Careers page / host -> {'status','ats','workday','n_ld','postings'} (JobPosting JSON-LD, ATS boards, Workday)."""
    res = {"status": "ok", "ats": [], "workday": [], "n_ld": 0, "postings": []}
    left = lambda: t_end - time.monotonic()
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _ROBOTS:   # one robots.txt read per host and run
        _ROBOTS[base] = J._robots(base, lambda u, timeout=10: J.get(u, timeout) if left() > 0 else None)[0]
    rp = _ROBOTS[base]
    if rp and not (rp.can_fetch(J.UA, url) or rp.can_fetch("*", url)):
        res["status"] = "robots"
        return res
    page = J.get(url, 12)
    if not page:
        res["status"] = "dead"
        return res
    html_, final = page
    blob = final + " " + html_
    res["ats"] = [list(a) for a in J.find_ats(blob) + find_extra_ats(blob)]
    res["workday"] = [list(w) for w in find_workday(blob)]
    posts, pages = [], [(html_, final)]
    for lu in J.find_listing_hosts(html_)[:2]:
        r = J.get(lu, 12) if left() > 0 else None
        if r:
            pages.append(r)

    def add_ld(h, u):
        found = J.parse_jobpostings(h, u)
        res["n_ld"] += len(found)
        posts.extend({**q, "src": "jsonld"} for q in found if J.is_fresh(q, max_age=o["max_age_days"]) and classify(q["title"]))

    for h, u in pages:
        add_ld(h, u)
    details = [d for h, u in pages for d in J.detail_links(h, u, classify)]
    for u, _ in list(dict.fromkeys(details))[: o["detail_cap"]]:
        r = J.get(u, 12) if left() > 0 else None
        if r:
            add_ld(*r)
    for ats, slug in res["ats"][:3]:
        if left() > 0:
            try:
                posts += extra_ats_rows(ats, slug, classify) if ats in ("gem", "rippling") else J._ats_rows(ats, slug, classify)
            except (ValueError, TypeError, KeyError):
                pass
    for tenant, wd, site in res["workday"][:2]:
        if left() > 0:
            try:
                posts += workday_rows(tenant, wd, site, keywords, classify, o["workday_cap"])
            except Exception:
                pass
    res["postings"] = list({q["url"]: q for q in posts if q.get("url")}.values())
    return res


# ---------------------------------------------------------------- target domains
_NOT_EMPLOYER = re.compile(
    r"(?:greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com|workable\.com|linkedin\.com|indeed\.|jooble|adzuna|"
    r"remoteok|weworkremotely|arbeitnow|jobicy|remotive|djinni|t\.me|google\.|relocate\.me|careerpuck|personio|recruitee|teamtailor|"
    r"bamboohr|breezy\.hr|join\.com|himalayas|themuse|wellfound|ycombinator|workatastartup|hh\.ru|glassdoor|ziprecruiter|github\.|"
    r"news\.ycombinator|reddit\.com|mastodon|bing\.com|oraclecloud|icims\.com|taleo\.net|successfactors|jobvite|eightfold|"
    r"dayforcehcm|paylocity|ultipro|adp\.com|gem\.com|rippling|pinpointhq|dover\.com|notion\.site|simplify\.jobs)", re.I)
_SLD = {"co", "com", "org", "net", "ac", "gov", "edu"}


def registered_domain(host):
    """eTLD+1 without a public-suffix list: last two labels, three under co.uk / com.au / ... (ponytail: .xx.yy suffixes only)."""
    labels = re.sub(r"^www\.", "", (host or "").lower().strip(".")).split(".")
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD else 2
    return ".".join(labels[-n:]) if len(labels) >= n else ""


def load_domains(db_path=None):
    """{domain: {name, prio}} - prio 0 = security company with no board found yet, 1 = employer already in jobs.db,
    2 = other security seeds and findall_domains.json, 3 = other seed files."""
    out = {}

    def add(domain, name, prio):
        domain = registered_domain(domain)
        if domain and DOMAIN_RE.match(domain) and not _NOT_EMPLOYER.search(domain):
            cur = out.setdefault(domain, {"name": name or domain.split(".")[0].title(), "prio": prio})
            cur["prio"] = min(cur["prio"], prio)

    try:
        miss = {k for k, v in json.loads((CACHE / "security_osint.json").read_text(encoding="utf-8")).items() if not v.get("board")}
    except (OSError, ValueError):
        miss = set()
    for f in sorted(SEEDS.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            for c in d.get("companies") or []:
                if isinstance(c, dict) and c.get("domain"):
                    add(c["domain"], c.get("name"), 0 if (c.get("name") or "").lower() in miss else 2)
        elif isinstance(d, list):
            prio = 2 if f.name == "findall_domains.json" else 3   # our own hand-kept list outranks the bulk jsonld seeds
            for c in d:
                if isinstance(c, str):
                    add(c, "", prio)
                elif isinstance(c, dict):
                    add(c.get("domain") or urllib.parse.urlparse(c.get("site") or "").netloc, c.get("name"), prio)
    try:
        con = sqlite3.connect(f"file:{db_path or ROOT / 'autopilot/jobs.db'}?mode=ro", uri=True)
        hosts = {}
        for url, company in con.execute("SELECT url, company FROM jobs WHERE status IN ('queued','new','applied')"):
            h = urllib.parse.urlparse(url or "").netloc
            if h and not _NOT_EMPLOYER.search(h):
                hosts.setdefault(registered_domain(h), company)
        con.close()
        for h, company in hosts.items():
            add(h, company, 1)
    except sqlite3.Error:
        pass
    return out


# ---------------------------------------------------------------- store + crawl with tracking
_SAVE = threading.Lock()


def load_store():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"domains": {}, "entries": {}, "boards": {}}


def save_store(store):
    with _SAVE:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STORE)


def _age(iso, today):
    try:
        return (today - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return 10**6


def entry_due(e, o, today):
    """Never read -> due; a host that produced new postings recently is re-read sooner than a quiet one; a dead one rarely."""
    if not e:
        return True
    if e.get("status") in ("dead", "robots"):
        return _age(e["t"], today) >= o["dead_recheck_days"]
    active = _age(e.get("last_new"), today) <= 30
    return _age(e["t"], today) >= (o["recheck_days"] if active else o["quiet_recheck_days"])


def merge_tracking(old, new_postings, today):
    """first_seen survives re-reads; returns ({url: posting}, number of postings never seen before)."""
    merged, fresh = {}, 0
    for q in new_postings:
        was = (old or {}).get(q["url"])
        q = {**q, "description": (q.get("description") or "")[:6000], "first_seen": was["first_seen"] if was else today.isoformat()}
        fresh += not was
        merged[q["url"]] = q
    return merged, fresh


def process_domain(domain, meta, store, classify, keywords, o, t_end, today):
    d = store["domains"].get(domain)
    if not d or _age(d.get("t"), today) >= o["domain_recheck_days"]:
        names = fetch_ct(domain, o["ct_gap_s"]) if o["ct"] else set()
        rows = fetch_cdx(domain, o["cdx_gap_s"]) if o["wayback"] else None
        d = store["domains"][domain] = {
            "t": today.isoformat(), "name": meta["name"], "ct_names": len(names), "cdx_ok": rows is not None,
            "hosts": [f"https://{h}/" for h in career_hosts(names, domain)[:3]],
            "urls": cdx_entries(rows or [], domain)}
    entries = list(dict.fromkeys(d["hosts"] + d["urls"]))[: o["max_entries_per_domain"]]
    new = 0
    for url in entries:
        old = store["entries"].get(url)
        if not entry_due(old, o, today) or time.monotonic() > t_end:
            continue
        try:
            r = read_entry(url, classify, keywords, o, min(t_end, time.monotonic() + 90))
        except Exception as exc:
            r = {"status": f"error:{str(exc)[:50]}", "ats": [], "workday": [], "n_ld": 0, "postings": []}
        posts, fresh = merge_tracking((old or {}).get("postings"), r["postings"], today)
        for q in posts.values():
            q["company"] = q.get("company") or meta["name"]
            q["persona"] = classify(q["title"])
        new += fresh
        store["entries"][url] = {"t": today.isoformat(), "domain": domain, "name": meta["name"], "status": r["status"],
                                 "ats": r["ats"], "workday": r["workday"], "n_ld": r["n_ld"], "postings": posts,
                                 "last_new": today.isoformat() if fresh else (old or {}).get("last_new", "")}
    return new


def crawl(cfg, classify, o, log=None):
    store, today = load_store(), date.today()
    store.setdefault("boards", {})
    doms = load_domains()
    order = sorted(doms.items(), key=lambda kv: (kv[1]["prio"], zlib.crc32(kv[0].encode())))
    # a domain is due when its host list is stale or one of its entries is due
    def due(dom):
        d = store["domains"].get(dom)
        return (not d or _age(d.get("t"), today) >= o["domain_recheck_days"]
                or any(entry_due(store["entries"].get(u), o, today) for u in (d["hosts"] + d["urls"])[: o["max_entries_per_domain"]]))
    todo = [kv for kv in order if due(kv[0])][: o["domains_per_run"]]
    keywords = list(dict.fromkeys(k for p in (cfg.get("personas") or {}).values()
                                  for k in (p.get("search", {}).get("keywords") or [])[:3]))[:6]
    t_end = time.monotonic() + o["budget_s"]
    stats = {"domains": len(doms), "due": len(todo), "new": 0, "done": 0}

    def run(kv):
        if time.monotonic() > t_end:
            return 0
        n = process_domain(kv[0], kv[1], store, classify, keywords, o, t_end, today)
        stats["done"] += 1
        stats["new"] += n
        if stats["done"] % 20 == 0:
            save_store(store)
            if log:
                log(f"{stats['done']}/{len(todo)} domains, {stats['new']} new postings")
        return n

    with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
        list(ex.map(run, todo))
    save_store(store)
    SUMMARY["findall_hosts"] = {**stats, "entries": len(store["entries"]),
                                "with_ct_host": sum(1 for d in store["domains"].values() if d["hosts"]),
                                "with_wayback_url": sum(1 for d in store["domains"].values() if d["urls"])}
    return store


# ---------------------------------------------------------------- import of FindAll's own stores
def read_findall(root, today):
    """[(row, observed)] from jobfind.db files and jobs CSV reports; row keys: title company location url description posted."""
    root, rows = Path(root), []
    for db in (root / "jobfind.db", root / "jobfind/jobfind.db"):
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            for r in con.execute("SELECT title,company,location,url,description,posted,first_seen FROM jobs"):
                rows.append(({**dict(r), "src": "findall:jobfind.db"}, (r["first_seen"] or "")[:10]))
            con.close()
        except sqlite3.Error:
            continue
    seen_files = 0
    for f in glob.glob(str(root / "**/*jobs_*.csv"), recursive=True):
        if re.search(r"check|valid|verify|test|apex", str(Path(f).relative_to(root)), re.I):   # FindAll self-test output
            continue
        seen_files += 1
        stamp = datetime.fromtimestamp(Path(f).stat().st_mtime).date().isoformat()
        try:
            with open(f, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("url"):
                        rows.append(({**{k: r.get(k) or "" for k in ("title", "company", "location", "url", "posted")},
                                      "description": "", "src": "findall:report"}, stamp))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    SUMMARY.setdefault("findall_import", {})["reports"] = seen_files
    return rows


def import_postings(cfg, classify, o, store=None):
    today = date.today()
    rows = read_findall(o["root"], today)
    boards, leads = {}, {}
    for r, observed in rows:
        r["company"] = html.unescape(r.get("company") or "")
        for ats, slug in J.find_ats(r.get("url") or ""):
            boards[(ats, slug)] = r["company"]
        posted = (r.get("posted") or "")[:10]
        age = _age(posted if re.match(r"\d{4}-\d\d-\d\d", posted) else observed, today)
        if r.get("description") and age <= o["max_age_days"] and r.get("url"):
            leads[r["url"]] = {**r, "first_seen": observed}
    store = store if store is not None else load_store()
    bs = store.setdefault("boards", {})
    todo = [(k, c) for k, c in boards.items() if _age(bs.get(f"{k[0]}:{k[1]}", {}).get("t"), today) >= o["board_recheck_days"]]
    todo = todo[: o["boards_per_run"]]

    def read(item):
        (ats, slug), company = item
        try:
            return ats, slug, J._ats_rows(ats, slug, classify), company
        except Exception:
            return ats, slug, [], company

    with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
        for ats, slug, posts, company in ex.map(read, todo):
            prev = bs.get(f"{ats}:{slug}", {})
            merged, fresh = merge_tracking(prev.get("postings"), [{**q, "company": q.get("company") or company} for q in posts], today)
            bs[f"{ats}:{slug}"] = {"t": today.isoformat(), "n": len(posts), "new": fresh, "postings": merged,
                                   "last_new": today.isoformat() if fresh else prev.get("last_new", "")}
    save_store(store)
    out = list(leads.values())
    for v in bs.values():
        out.extend(v.get("postings", {}).values())
    SUMMARY["findall_import"] = {"rows": len(rows), "boards_named": len(boards), "boards_read": len(todo),
                                 "reports": SUMMARY.get("findall_import", {}).get("reports", 0)}
    return out


# ---------------------------------------------------------------- the discover entry point
def settings(cfg):
    base = ((cfg or {}).get("discovery") or {}).get("findall_osint") or {}
    return {"enabled": base.get("enabled", True),
            "import": {**DEFAULTS["import"], **(base.get("import") or {})},
            "hosts": {**DEFAULTS["hosts"], **(base.get("hosts") or {})}}


_LAST = {}


def _fresh_cached(store, o, today):
    for e in store["entries"].values():
        for q in e.get("postings", {}).values():
            if J.is_fresh(q, today, o["max_age_days"]) and (q.get("posted") or _age(q["first_seen"], today) <= o["max_age_days"]):
                yield q


def discover(name, cfg, classify, europe_location, visa_ok=None):
    """Postings of cfg["active_persona"] from `findall_import` / `findall_hosts` (see module doc)."""
    s = settings(cfg)
    if not s["enabled"] or name not in SOURCES:
        return []
    persona, today = (cfg or {}).get("active_persona"), date.today()
    if name == "findall_import":
        o = s["import"]
        if not o["enabled"]:
            return []
        if time.monotonic() - _LAST.get(name, -1e9) > 600:
            _LAST[name] = time.monotonic()
            _LAST[name + "_rows"] = import_postings(cfg, classify, o)
        raw, limit = _LAST[name + "_rows"], o["max_items"]
        fresh_days = o["max_age_days"]
    else:
        o = s["hosts"]
        if not o["enabled"]:
            return []
        if time.monotonic() - _LAST.get(name, -1e9) > 600:   # discover asks once per persona: crawl once per run
            _LAST[name] = time.monotonic()
            crawl(cfg, classify, o, log=lambda m: print(f"findall_hosts: {m}", file=sys.stderr, flush=True))
        raw, limit, fresh_days = list(_fresh_cached(load_store(), o, today)), o["max_items"], o["max_age_days"]
    out, seen = [], set()
    for q in raw:
        url, title = q.get("url") or "", (q.get("title") or "").strip()
        if not url or not title or url in seen or not J.is_fresh(q, today, fresh_days):
            continue
        if classify(title) != persona:
            continue
        job = {"title": title, "company": q.get("company") or "", "url": url, "location": q.get("location") or "",
               "persona": persona, "description": (q.get("description") or "") +
               (f"\nPosted: {q['posted']}" if q.get("posted") else "") + f"\nFirst seen: {q.get('first_seen', '')} ({name})"}
        if persona == "creative" and europe_location:
            ok = (not job["location"]) or europe_location(job) or _REMOTE.search(job["location"])
        else:
            ok = visa_ok(job, persona) if visa_ok else True
        if ok:
            seen.add(url)
            out.append(job)
    SUMMARY.setdefault(name, {})["kept"] = len(out)
    return out[:limit]


def _cli():
    cmd = sys.argv[1:2]
    if cmd == ["stats"]:
        st = load_store()
        ents = st["entries"].values()
        print(f"{len(st['domains'])} domains read; {sum(1 for d in st['domains'].values() if d['hosts'])} with a CT career host, "
              f"{sum(1 for d in st['domains'].values() if d['urls'])} with a Wayback career URL; {len(st['entries'])} entries, "
              f"{sum(len(e['postings']) for e in ents)} postings, {sum(1 for e in ents if e.get('last_new'))} entries with new postings; "
              f"{len(st.get('boards', {}))} imported boards; {len(load_domains())} target domains")
    elif cmd == ["domains"]:
        for d, m in sorted(load_domains().items(), key=lambda kv: kv[1]["prio"])[:40]:
            print(m["prio"], d, m["name"])
    elif cmd == ["run"]:
        import autopilot as A
        cfg = A.read_json(A.CFG)
        o = settings(cfg)["hosts"]
        if "--domains" in sys.argv:
            o["domains_per_run"] = int(sys.argv[sys.argv.index("--domains") + 1])
        crawl(cfg, lambda t: A.classify_persona(t, cfg), o, log=lambda m: print(m, flush=True))
        print(SUMMARY)
    else:
        sys.exit("usage: findall_osint.py stats | domains | run [--domains N]   (real run: autopilot.py discover --source findall_hosts)")


if __name__ == "__main__":
    _cli()
