"""OSINT discovery sources: postings that never reach the aggregators.

Five `discover --source` names, all public pages / open APIs, no login, no captcha or
anti-bot bypass, one polite request per `gap` seconds per host:

  arbeitsagentur  Bundesagentur fuer Arbeit Jobsuche API (all German postings, title search)
  eu_portals      EURES public job-vacancy search (EU/EEA public employment services)
  hn_hiring       Hacker News "Who is hiring?" / "Freelancer?" threads + HN jobs (Algolia, Firebase)
  dorks           the `site:` dork done without a search engine (they all show captchas):
                  the Common Crawl URL index lists every ATS board slug it ever saw
                  (job-boards.greenhouse.io/*, jobs.lever.co/*, apply.workable.com/* ...); the boards
                  not yet in the bot are read through their public ATS API, a batch per run
  community       public Reddit (Atom) and Mastodon hashtag (RSS) feeds

`discover(name, cfg, classify, europe_location)` returns dicts (title, company, location, url,
description) for the persona in cfg["active_persona"]. Settings: config.json
discovery.<name>; seeds: autopilot/seeds/osint_*.json; caches: autopilot/cache/osint_<name>.json.
"""
import base64
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

try:
    import defusedxml.ElementTree as ET   # feeds are third-party XML
except ImportError:   # pragma: no cover
    import xml.etree.ElementTree as ET

import creative_sources as CS

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "autopilot/cache"
SEEDS = ROOT / "autopilot/seeds"
DB = ROOT / "autopilot/jobs.db"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
SOURCES = ("arbeitsagentur", "eu_portals", "hn_hiring", "dorks", "community")
DEFAULTS = {
    "arbeitsagentur": {"enabled": True, "max_age_days": 14, "pages": 2, "page_size": 100, "details_per_run": 150, "gap": 0.4},
    "eu_portals": {"enabled": True, "publication_period": "LAST_WEEK", "pages": 2, "page_size": 50,
                   "skip_countries": ["DE"], "gap": 0.8},   # DE: arbeitsagentur has the richer copy
    "hn_hiring": {"enabled": True, "months": 3, "jobstories": 40, "gap": 0.5},
    "dorks": {"enabled": True, "boards_per_run": 200, "recheck_days": 4, "dead_recheck_days": 45, "workers": 8,
              "use_db_urls": True, "crawl_pages_per_run": 4, "max_pages_per_host": 12, "crawl_refresh_days": 30, "boards": {}, "gap": 1.0},
    "community": {"enabled": True, "gap": 5.0, "fail_skip_days": 3},
}
SUMMARY = {}          # source -> {"raw": n, "kept": n, ...} of the last call
_MEMO = {}            # persona-independent fetches shared by the per-persona passes of one process
_US_ONLY = re.compile(r"\b(?:us|usa|u\.s\.?|united states|canada)\s*(?:only|-only|based|residents?)\b|\bremote\s*[\(\-,/]\s*(?:us|usa|canada)\b", re.I)
_LAST, _LOCK = {}, threading.Lock()


# ---------- plumbing ----------
def _get(url, data=None, headers=None, timeout=30, gap=1.0):
    """One GET/POST, at most one request per `gap` seconds to the same host (all threads)."""
    host = urllib.parse.urlparse(url).netloc
    with _LOCK:
        slot = max(time.monotonic(), _LAST.get(host, 0) + gap)
        _LAST[host] = slot
    if slot > time.monotonic():
        time.sleep(slot - time.monotonic())
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _load(name):
    try:
        return json.loads((CACHE / f"osint_{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(name, obj):
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = CACHE / f"osint_{name}.json.tmp"
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE / f"osint_{name}.json")
    except OSError as exc:
        print(f"osint: cache {name} not saved ({exc})", file=sys.stderr, flush=True)


def _now():
    return datetime.now(timezone.utc)


def _age_days(iso):
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds() / 86400
    except (TypeError, ValueError):
        return 1e9


def _seed(file):
    try:
        return json.loads((SEEDS / file).read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError as exc:
        print(f"osint: seed {file} is not valid JSON ({exc})", file=sys.stderr, flush=True)
        return {}


def settings(cfg, name):
    return {**DEFAULTS[name], **(((cfg or {}).get("discovery") or {}).get(name) or {})}


def queries(cfg, name, persona):
    """Seed queries of a persona; config discovery.<name>.queries.<persona> replaces them,
    .extra_queries.<persona> adds to them."""
    s = settings(cfg, name)
    base = (s.get("queries") or {}).get(persona)
    if base is None:
        base = _seed("osint_queries.json").get(name, {}).get(persona, [])
    return list(dict.fromkeys(list(base) + list((s.get("extra_queries") or {}).get(persona, []))))


def _text(raw):
    return CS._text(raw)


def _finish(rows, persona, classify, europe_location, src):
    """Rows of this persona's profession, in Europe / remote (or with no stated place)."""
    out, seen = [], set()
    for r in rows:
        t, u, loc = (r.get("title") or "").strip(), r.get("url") or "", r.get("location") or ""
        same = ((r.get("company") or "").lower(), t.lower(), loc.lower())   # the same ad re-posted under a new id
        if not t or not u or u in seen or same in seen or classify(t) != persona or _US_ONLY.search(loc):
            continue
        if loc and not (europe_location({"location": loc}) or CS._REMOTE_RE.search(loc)):
            continue
        seen.update((u, same))
        out.append({**r, "title": t, "src": r.get("src", src)})
    return out


def _memo(key, fn):
    if key not in _MEMO:
        _MEMO[key] = fn()
    return _MEMO[key]


# ---------- arbeitsagentur ----------
BA = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
BA_KEY = {"X-API-Key": "jobboerse-jobsuche"}   # the public key of BA's own web app (bund.dev docs)


def _ba_location(j):
    a = ((j.get("stellenlokationen") or [{}])[0].get("adresse")) or {}
    return ", ".join(x for x in (a.get("ort"), (a.get("region") or "").replace("_", " ").title(), "Germany") if x)


def _pmap(fn, items, workers=4):
    """fn over items in a small pool; `_get` spaces the request starts per host, so this stays polite."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def run_arbeitsagentur(cfg, s, persona, classify, europe_location):
    cache = _load("arbeitsagentur")
    stats = {"queries": 0, "listed": 0, "details": 0, "errors": 0}

    def page(qp):
        q, pg = qp
        url = f"{BA}/v6/jobs?" + urllib.parse.urlencode({
            "was": q, "angebotsart": 1, "veroeffentlichtseit": s["max_age_days"], "size": s["page_size"],
            "page": pg, "pav": "false", "zeitarbeit": "false"})
        try:
            return json.loads(_get(url, headers=BA_KEY, gap=s["gap"]))
        except Exception as exc:
            stats["errors"] += 1
            print(f"arbeitsagentur: {q!r} p{pg}: {exc}", file=sys.stderr, flush=True)
            return {}
    qs = queries(cfg, "arbeitsagentur", persona)
    cur, pages = _pmap(page, [(q, 1) for q in qs]), []
    for pg in range(2, int(s["pages"]) + 2):   # deeper pages only for queries that have more results
        pages += cur
        more = [q for q, d in zip(qs, cur) if (d.get("maxErgebnisse") or 0) > (pg - 1) * int(s["page_size"])]
        qs, cur = more, _pmap(page, [(q, pg) for q in more]) if pg <= int(s["pages"]) and more else []
    stats["queries"] = len([d for d in pages if d])
    found, items = set(), []
    for d in pages:
        for j in d.get("ergebnisliste") or []:
            ref, title = j.get("referenznummer"), j.get("stellenangebotsTitel")
            if ref and title and ref not in found and j.get("stellenangebotsart") == "ARBEIT" and classify(title) == persona:
                found.add(ref)
                items.append(j)
    stats["listed"] = len(items)
    todo = [j["referenznummer"] for j in items if j["referenznummer"] not in cache][: int(s["details_per_run"])]

    def detail(ref):
        try:
            dd = json.loads(_get(f"{BA}/v4/jobdetails/{base64.b64encode(ref.encode()).decode()}", headers=BA_KEY, gap=s["gap"]))
            return ref, {"t": _now().isoformat(), "desc": _text(dd.get("stellenangebotsBeschreibung")),
                         "ext": dd.get("externeURL") or "", "home": bool(dd.get("homeofficemoeglich"))}
        except Exception:
            stats["errors"] += 1
            return ref, None
    for ref, e in _pmap(detail, todo):
        if e:
            cache[ref] = e
            stats["details"] += 1
    rows = []
    for j in items:
        e = cache.get(j["referenznummer"]) or {}
        loc = _ba_location(j) + (" (home office possible)" if e.get("home") else "")
        rows.append({"title": j["stellenangebotsTitel"], "company": j.get("firma") or "", "location": loc,
                     "description": e.get("desc", "") + (f"\n\nEmployer posting: {e['ext']}" if e.get("ext") else ""),
                     "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{j['referenznummer']}", "apply_url": e.get("ext", "")})
    _save("arbeitsagentur", {k: v for k, v in cache.items() if _age_days(v.get("t")) < 60})
    SUMMARY["arbeitsagentur"] = stats
    return rows


# ---------- eu_portals (EURES) ----------
EURES = "https://europa.eu/eures/api/jv-searchengine/public"
_EURES_COUNTRY = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark",
    "EE": "Estonia", "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IS": "Iceland",
    "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LI": "Liechtenstein", "LT": "Lithuania", "LU": "Luxembourg",
    "MT": "Malta", "NL": "Netherlands", "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "CH": "Switzerland"}


def run_eu_portals(cfg, s, persona, classify, europe_location):
    stats = {"queries": 0, "listed": 0, "errors": 0}
    skip = {c.upper() for c in s.get("skip_countries") or []}

    def one(q):
        out = []
        for page in range(1, int(s["pages"]) + 1):
            body = {"resultsPerPage": s["page_size"], "page": page, "sortSearch": "MOST_RECENT",
                    "keywords": [{"keyword": q, "specificSearchCode": "TITLE"}], "publicationPeriod": s["publication_period"],
                    "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [], "positionScheduleCodes": [],
                    "sectorCodes": [], "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
                    "locationCodes": [], "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [],
                    "minNumberPost": None, "sessionId": "osint"}
            try:
                d = json.loads(_get(f"{EURES}/jv-search/search", json.dumps(body).encode(),
                                    {"Content-Type": "application/json", "Accept": "application/json"}, gap=s["gap"]))
            except Exception as exc:
                stats["errors"] += 1
                print(f"eu_portals: {q!r}: {exc}", file=sys.stderr, flush=True)
                break
            stats["queries"] += 1
            out += d.get("jvs") or []
            if len(d.get("jvs") or []) < int(s["page_size"]):
                break
        return out
    rows, seen = [], set()
    for jvs in _pmap(one, queries(cfg, "eu_portals", persona)):
        for jv in jvs:
            title, jid = jv.get("title"), jv.get("id")
            codes = list((jv.get("locationMap") or {}).keys())
            if not title or not jid or jid in seen or classify(title) != persona or (codes and set(codes) <= skip):
                continue
            seen.add(jid)
            rows.append({"title": title, "company": (jv.get("employer") or {}).get("name") or "",
                         "location": ", ".join(_EURES_COUNTRY.get(c, c) for c in codes if c not in skip),
                         "description": _text(jv.get("description")),
                         "url": f"https://europa.eu/eures/portal/jv-se/jv-details/{jid}"})
    stats["listed"] = len(rows)
    SUMMARY["eu_portals"] = stats
    return rows


# ---------- hn_hiring ----------
ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
_HN_NOISE = re.compile(r"news\.ycombinator|twitter\.com|x\.com/|linkedin\.com/in/|youtu|imgur|wikipedia", re.I)
_HN_JOBLINK = re.compile(r"greenhouse|lever\.co|ashby|workable|smartrecruiters|recruitee|personio|teamtailor|join\.com|/jobs|/careers|career", re.I)


def _hn_prefilter(cfg):
    terms = set()
    for persona, qs in _seed("osint_queries.json").get("hn_hiring", {}).items():
        terms |= {q.lower() for q in (settings(cfg, "hn_hiring").get("queries") or {}).get(persona, qs)}
    for qs in (settings(cfg, "hn_hiring").get("extra_queries") or {}).values():
        terms |= {q.lower() for q in qs}
    return terms


def _hn_comment(c):
    """(plain text, [links]) of a top-level comment; None when deleted."""
    raw = c.get("text") or ""
    if not raw or c.get("deleted") or c.get("dead"):
        return None
    links = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', raw)]
    plain = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<p>|<br\s*/?>", "\n", raw)))
    return re.sub(r"[ \t\xa0]+", " ", plain).strip(), links


def hn_rows(entries, classify):
    """HN comment entries -> raw rows. Header 'Company | Role | Place | REMOTE' (free-form)."""
    out = []
    for e in entries:
        lines = [l.strip() for l in e["text"].split("\n") if l.strip()]
        if not lines or re.search(r"seeking work|for hire", lines[0][:60], re.I):
            continue
        parts = [p.strip() for p in re.split(r"\s*[|•·]\s*", lines[0]) if p.strip()]
        cands = parts[1:] + [l for l in lines[:14] if len(l) <= 120] + [x.strip() for x in re.split(r"[;,]", lines[0]) if 3 < len(x) < 80]
        title = next((c for c in cands if classify(c)), None)
        if not title:
            continue
        links = [l for l in e.get("links", []) if not _HN_NOISE.search(l)]
        url = next((l for l in links if _HN_JOBLINK.search(l)), links[0] if links else f"https://news.ycombinator.com/item?id={e['id']}")
        title = title.lstrip(" *-•·")
        out.append({"title": title[:120], "company": (parts[0] if parts else "")[:80], "location": " | ".join(parts[1:])[:160] or lines[0][:160],
                    "description": e["text"][:3500], "url": url})
    return out


def run_hn_hiring(cfg, s, persona, classify, europe_location):
    def fetch():
        cache = _load("hn_hiring")
        terms = _hn_prefilter(cfg)
        stats = {"threads": 0, "comments": 0, "errors": 0, "jobstories": 0}
        entries = []
        try:
            hits = json.loads(_get(f"{ALGOLIA}/search_by_date?tags=story,author_whoishiring&hitsPerPage=30", gap=s["gap"])).get("hits") or []
        except Exception as exc:
            hits, stats["errors"] = [], stats["errors"] + 1
            print(f"hn_hiring: thread list: {exc}", file=sys.stderr, flush=True)
        threads = cache.setdefault("threads", {})
        for h in hits:
            age = (_now().timestamp() - (h.get("created_at_i") or 0)) / 86400
            if not re.search(r"who is hiring|freelancer", h.get("title") or "", re.I) or age > 31 * int(s["months"]):
                continue
            tid, t = str(h["objectID"]), threads.get(str(h["objectID"]))
            if t and (age > 35 or _age_days(t["t"]) < 0.5):
                entries += t["entries"]
                continue
            try:
                tree = json.loads(_get(f"{ALGOLIA}/items/{tid}", timeout=90, gap=s["gap"]))
            except Exception as exc:
                stats["errors"] += 1
                print(f"hn_hiring: thread {tid}: {exc}", file=sys.stderr, flush=True)
                entries += (t or {}).get("entries", [])
                continue
            es = []
            for c in tree.get("children") or []:
                p = _hn_comment(c)
                if p and any(w in p[0].lower() for w in terms):
                    es.append({"id": c.get("id"), "text": p[0][:4000], "links": p[1][:6]})
            threads[tid] = {"t": _now().isoformat(), "title": h.get("title"), "entries": es}
            stats["threads"] += 1
            stats["comments"] += len(tree.get("children") or [])
            entries += es
        rows = hn_rows(entries, classify)
        # YC "X is hiring a Y" job stories (ids immutable, cached)
        stories = cache.setdefault("jobs", {})
        try:
            ids = json.loads(_get(f"{FIREBASE}/jobstories.json", gap=s["gap"]))[: int(s["jobstories"])]
        except Exception:
            ids = []
        for i in ids:
            if str(i) not in stories:
                try:
                    it = json.loads(_get(f"{FIREBASE}/item/{i}.json", gap=s["gap"])) or {}
                    stories[str(i)] = {"t": _now().isoformat(), "title": it.get("title") or "", "url": it.get("url") or f"https://news.ycombinator.com/item?id={i}"}
                    stats["jobstories"] += 1
                except Exception:
                    stats["errors"] += 1
        for i in ids:
            st = stories.get(str(i))
            m = st and re.match(r"(.+?)\s+(?:\(YC [^)]*\)\s*)?is hiring\s+(?:an?\s+|our\s+|the\s+)?(.+)", st["title"], re.I)
            if m and classify(m.group(2)):
                rows.append({"title": m.group(2)[:120], "company": m.group(1)[:80], "location": "", "description": st["title"], "url": st["url"]})
        for k in [k for k, v in stories.items() if _age_days(v["t"]) > 90]:
            stories.pop(k)
        for k in [k for k, v in threads.items() if _age_days(v["t"]) > 31 * int(s["months"]) + 10]:
            threads.pop(k)
        _save("hn_hiring", cache)
        SUMMARY["hn_hiring"] = {**stats, "raw": len(rows)}
        return rows
    return _memo("hn_hiring", fetch)


# ---------- dorks: board discovery through the Common Crawl URL index ----------
CC = "https://index.commoncrawl.org"
_SLUG_NOISE = {"embed", "jobs", "api", "v1", "static", "assets", "careers", "robots.txt", "favicon.ico", "en", "www", "boards",
               "job", "apply", "widget", "cdn", "images", "img", "js", "css", "fonts", "sitemap.xml", "login", "sign-in", "about"}
_NOT_FOUND = (404, 410)


def slugs_from_index(text, host):
    """Distinct board slugs in the CDX response `text` for a seed host entry."""
    rx = re.compile(host["rx"], re.I)
    out = set()
    for m in rx.findall(text.replace("\\/", "/")):
        slug = urllib.parse.unquote(m)
        slug = slug if host["ats"] in ("ashby", "smartrecruiters") else slug.lower()
        if len(slug) >= 2 and slug.lower() not in _SLUG_NOISE and re.fullmatch(r"[\w.-]+", slug):
            out.add(slug)
    return out


def _crawl(cache, s):
    """Advance the Common Crawl listing by `crawl_pages_per_run` pages; slugs accumulate in the cache."""
    crawl = cache.setdefault("crawl", {"hosts": {}, "slugs": {}})
    hosts = _seed("osint_feeds.json").get("ats_hosts", [])
    if crawl.get("coll_t") is None or _age_days(crawl["coll_t"]) > 7:
        try:
            crawl["coll"] = json.loads(_get(f"{CC}/collinfo.json", gap=s["gap"]))[0]["cdx-api"]
            crawl["coll_t"] = _now().isoformat()
        except Exception as exc:
            print(f"dorks: Common Crawl collection list: {exc}", file=sys.stderr, flush=True)
    if not crawl.get("coll"):
        return 0
    budget, fetched = int(s["crawl_pages_per_run"]), 0

    def progress(h):
        st = crawl["hosts"].get(h["pattern"]) or {}
        return st.get("fail", 0), st.get("next", 0)

    def cc_get(url):   # the index answers 502/504 under load: one retry, then the host waits for the next run
        try:
            return _get(url, timeout=90, gap=s["gap"])
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            time.sleep(5)
            return _get(url, timeout=90, gap=s["gap"])

    run_fails = 0
    for h in sorted(hosts, key=progress):   # fewest failures first, then least progress
        st = crawl["hosts"].setdefault(h["pattern"], {})
        last = min(st["pages"], int(s["max_pages_per_host"])) if st.get("pages") is not None else None
        done = last is not None and st.get("next", 0) >= last
        if (done and _age_days(st.get("t")) <= int(s["crawl_refresh_days"])) or (
                st.get("fail", 0) >= 3 and _age_days(st.get("ft")) < 1):
            continue
        if fetched >= budget or run_fails >= 2:
            break
        if done:
            st.update({"next": 0, "pages": None})   # refresh a finished host
        q = f"{crawl['coll']}?url={urllib.parse.quote(h['pattern'], safe='*/.')}&output=json"
        try:
            if st.get("pages") is None:
                st["pages"] = int(json.loads(cc_get(q + "&showNumPages=true"))["pages"])
            st.setdefault("next", 0)
            while fetched < budget and st["next"] < min(st["pages"], int(s["max_pages_per_host"])):
                text = cc_get(q + f"&fl=url&page={st['next']}")
                crawl["slugs"][h["ats"]] = sorted(set(crawl["slugs"].get(h["ats"], [])) | slugs_from_index(text, h))
                st["next"] += 1
                fetched += 1
            st["t"], st["fail"] = _now().isoformat(), 0
        except Exception as exc:
            run_fails += 1
            st["fail"], st["ft"] = st.get("fail", 0) + 1, _now().isoformat()
            print(f"dorks: Common Crawl {h['pattern']}: {exc}", file=sys.stderr, flush=True)
    return fetched


def _db_boards():
    """Board slugs of every ATS posting already stored (jobspy / LinkedIn / Google found them by title):
    their companies' other openings never surfaced. {ats: [slugs]}"""
    import sqlite3
    out = {}
    try:
        con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
        text = "\n".join(u for (u,) in con.execute("SELECT DISTINCT url FROM jobs") if u)
        con.close()
    except sqlite3.Error:
        return out
    for h in _seed("osint_feeds.json").get("ats_hosts", []):
        out.setdefault(h["ats"], set()).update(slugs_from_index(text, h))
    return {a: sorted(v) for a, v in out.items()}


_WK_SEM = threading.Semaphore(3)   # Workable answers 429 beyond a few parallel requests


def _smartrecruiters(slug, qs):
    out, seen = [], set()
    for q in qs:
        d = json.loads(CS._get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&q={urllib.parse.quote(q)}"))
        for j in d.get("content") or []:
            if j.get("id") not in seen:
                seen.add(j.get("id"))
                loc = j.get("location") or {}
                out.append(CS._row(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                                   ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x),
                                   company=(j.get("company") or {}).get("name") or ""))
    return out


def _fetch_board(ats, slug, sr_queries):
    if ats == "smartrecruiters":
        return _smartrecruiters(slug, sr_queries)
    if ats == "workable":
        for attempt in range(3):
            with _WK_SEM:
                try:
                    return CS.FETCHERS["workable"](slug)
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or attempt == 2:
                        raise
            time.sleep(4 * (attempt + 1))
    return CS.FETCHERS[ats](slug)


def run_dorks(cfg, s, persona, classify, europe_location):
    def fetch():
        cache = _load("dorks")
        crawled = _crawl(cache, s)
        known = {(a, v.lower()) for a, v in ((a, x) for a, xs in (cfg.get("vendor_boards") or {}).items() for x in xs)}
        known |= {(a, v.lower()) for a, xs in CS.CREATIVE_BOARDS.items() for v in xs}
        known |= {("lever", v.lower()) for v in (cfg.get("vendor_boards") or {}).get("lever_eu", [])}
        cand = {}
        for ats, xs in list((cache["crawl"].get("slugs") or {}).items()) + list((s.get("boards") or {}).items()) + list(_seed("ats_boards.json").items()) + list((_db_boards() if s.get("use_db_urls", True) else {}).items()):
            for x in xs:
                if (ats.replace("lever_eu", "lever"), x.lower()) not in known and ats in CS.FETCHERS:
                    cand[f"{ats}:{x}"] = (ats, x)
        boards = cache.setdefault("boards", {})

        def due(k):
            b = boards.get(k)
            if not b:
                return 0, ""
            wait = int(s["dead_recheck_days"]) if b.get("dead") else int(s["recheck_days"]) * (1 if b.get("hits") else 3)
            return (1, b["t"]) if _age_days(b["t"]) >= wait else (9, b["t"])
        order = sorted((k for k in cand if due(k)[0] < 9), key=lambda k: (due(k), hash_key(k)))
        batch = order[: int(s["boards_per_run"])]
        sr_q = (s.get("queries") or {}).get("smartrecruiters") or _seed("osint_queries.json").get("dorks", {}).get("smartrecruiters", [])
        rows, stats = [], {"crawled_pages": crawled, "candidate_boards": len(cand), "scanned": 0, "dead": 0, "errors": 0, "with_hits": 0}

        def work(k):
            ats, slug = cand[k]
            try:
                return k, _fetch_board(ats, slug, sr_q), None
            except urllib.error.HTTPError as exc:
                return k, None, exc.code
            except Exception as exc:
                return k, None, str(exc)
        with ThreadPoolExecutor(max_workers=int(s["workers"])) as ex:
            for k, got, err in ex.map(work, batch):
                stats["scanned"] += 1
                if got is None:
                    dead = err in _NOT_FOUND
                    stats["dead" if dead else "errors"] += 1
                    boards[k] = {"t": _now().isoformat(), "n": 0, "hits": 0, "dead": dead}
                    continue
                name = cand[k][1].replace("-", " ").title()
                hit = [{**r, "company": r["company"] or name, "src": f"{k}"} for r in got if r["title"] and r["url"] and classify(r["title"])]
                boards[k] = {"t": _now().isoformat(), "n": len(got), "hits": len(hit), "dead": False}
                stats["with_hits"] += bool(hit)
                rows += hit
        _save("dorks", cache)
        SUMMARY["dorks"] = {**stats, "raw": len(rows), "boards_total": len(boards), "boards_unchecked": sum(1 for k in cand if k not in boards)}
        return rows
    return _memo("dorks", fetch)


def hash_key(k):
    """Stable pseudo-random order, so unchecked boards are not read alphabetically."""
    return sum((i + 1) * ord(c) for i, c in enumerate(k)) % 100003


# ---------- community: Reddit Atom + Mastodon RSS ----------
_FEED_NOISE = re.compile(r"reddit\.com|redd\.it|imgur|giphy|youtu|twitter\.com|x\.com/|mastodon|fosstodon|infosec\.exchange|/tags/|/@", re.I)
_CUE = re.compile(r"\b(?:hiring|fedihire|we.re looking|looking for|seek(?:s|ing)|wanted|vacanc\w+|job|position|role|apply|open call|join (?:us|our))\b", re.I)
_FOR_HIRE = re.compile(r"for hire|available for|seeking work", re.I)
_TAG = re.compile(r"^\s*(?:\[[^\]]{1,20}\]\s*)+")


def parse_feed(xml):
    """[(title, link, html, author)] from an Atom (Reddit) or RSS (Mastodon) document."""
    root = ET.fromstring(xml)
    items = []
    for e in root.iter():
        tag = e.tag.rsplit("}", 1)[-1]
        if tag == "entry":
            link = next((l.get("href") for l in e if l.tag.endswith("link") and l.get("href")), "")
            items.append((e.findtext("{*}title") or "", link, e.findtext("{*}content") or "", e.findtext("{*}author/{*}name") or ""))
        elif tag == "item":
            items.append((e.findtext("title") or "", e.findtext("link") or "", e.findtext("description") or "", e.findtext("{*}creator") or ""))
    return items


def community_rows(items, feed, classify):
    out = []
    for title, link, body, author in items:
        text = _text(body)
        if _FOR_HIRE.search(title[:40]) or not _CUE.search(f"{title} {text[:600]}") or (feed.get("require") and not re.search(feed["require"], f"{title} {text[:400]}", re.I)):
            continue
        # a Reddit post names its role in the title; a toot only in its text
        sentences = [] if feed.get("kind") == "atom" else [
            x.strip() for x in re.split(r"[\n.!?]", re.sub(r"#\w+\s*", "", text)) if 3 < len(x.strip()) <= 120][:10]
        t = next((c for c in [_TAG.sub("", html.unescape(title)).strip(), *sentences] if c and classify(c)), None)
        if not t:
            continue
        ext = next((l for l in re.findall(r'href="(https?://[^"]+)"', html.unescape(body)) if not _FEED_NOISE.search(l)), "")
        host = urllib.parse.urlparse(ext).netloc.removeprefix("www.") if ext else ""
        out.append({"title": t[:120], "company": host or author.replace("/u/", "")[:60], "location": "", "url": ext or link,
                    "description": text[:3000], "feed": feed["name"]})
    return out


def run_community(cfg, s, persona, classify, europe_location):
    def fetch():
        cache = _load("community")
        feeds, rows, stats = _seed("osint_feeds.json").get("community", []), [], {"feeds": 0, "items": 0, "errors": 0}

        def one(f):
            st = cache.setdefault(f["name"], {})
            if st.get("fail") and _age_days(st["fail"]) < float(s["fail_skip_days"]):
                return []
            try:
                items = parse_feed(_get(f["url"], gap=s["gap"]))
                st.pop("fail", None)
            except Exception as exc:
                st["fail"] = _now().isoformat()
                stats["errors"] += 1
                print(f"community: {f['name']}: {exc}", file=sys.stderr, flush=True)
                return []
            stats["feeds"] += 1
            stats["items"] += len(items)
            return community_rows(items, f, classify)
        for part in _pmap(one, feeds):
            rows += part
        _save("community", cache)
        SUMMARY["community"] = {**stats, "raw": len(rows)}
        return rows
    return _memo("community", fetch)


RUNNERS = {"arbeitsagentur": run_arbeitsagentur, "eu_portals": run_eu_portals, "hn_hiring": run_hn_hiring,
           "dorks": run_dorks, "community": run_community}


def discover(name, cfg, classify, europe_location):
    """Postings of cfg["active_persona"] from source `name` (see module doc)."""
    s = settings(cfg, name)
    if not s.get("enabled", True):
        return []
    persona = (cfg or {}).get("active_persona")
    rows = RUNNERS[name](cfg or {}, s, persona, classify, europe_location)
    out = _finish(rows, persona, classify, europe_location, name)
    if name in ("arbeitsagentur", "eu_portals") and s.get("resolve_employer", True):   # aggregator page has no form: find the employer's
        import employer_url
        out = employer_url.apply_to_rows(out, name, int(s.get("resolve_per_run", 200)))
    SUMMARY.setdefault(name, {})["kept"] = len(out)
    return out[: int(s.get("max_items", 2000))]
