"""Security-employer OSINT: find AppSec / pentest / red-team / DevSecOps postings that never
reach an aggregator, by walking the careers boards of cybersecurity employers.

  1. company seeds   autopilot/seeds/security_<list>.json, shipped in the repo and refreshable:
        curated   hand-kept vendors, boutiques, MSSPs, security-heavy fintech/crypto/SaaS
        wikidata  Wikidata SPARQL: companies in the computer-security / infosec industries
        ecso      European Cyber Security Organisation member list (ecs-org.eu)
        crest     CREST marketplace suppliers (pentest / red-team / IR firms, public directory)
        bounty    owners of public bug-bounty / VDP programmes (Intigriti, YesWeHack, HackerOne)
        register  UK / NL sponsor-register names that look like cyber firms
        db        (dynamic) companies already in jobs.db with a security-persona title
  2. resolve each company to an ATS board: its careers page is scraped for Greenhouse / Lever /
     Ashby / Workable / SmartRecruiters / Recruitee / Personio / Teamtailor links (and JobPosting
     JSON-LD), else the board is probed under slugs taken from its domain and name
  3. read all postings, keep title fits for the persona (mid/junior; seniors are excluded by
     the persona's own title terms) that have a real visa path (sponsors.visa_path)
  4. results and misses are cached (autopilot/cache/security_osint.json) so reruns are incremental

Legal and polite: public pages / JSON APIs only, no login, no anti-bot bypass, at most
`rps` requests per second per host.

  python tools/security_osint.py --refresh-seeds [--list wikidata crest ...]   rebuild seed files
  python tools/security_osint.py --stats                                        what the seeds/cache hold
  python tools/security_osint.py --run [--max-companies N]                      dry run, prints postings
  python tools/autopilot.py discover --source security_osint                    real ingest

Config (autopilot/config.json): discovery.security_osint = {enabled, max_companies_per_run,
lists, workers, rps, miss_retry_days}."""
import argparse, collections, html, json, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "autopilot/seeds"
CACHE_FILE = ROOT / "autopilot/cache/security_osint.json"
LISTS = ("curated", "wikidata", "ecso", "crest", "bounty", "register", "db")
DEFAULTS = {"enabled": True, "max_companies_per_run": 300, "lists": list(LISTS), "workers": 16, "rps": 2.0,
            "miss_retry_days": 45}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
SUMMARY = {}

# ---------- polite HTTP: <= rps requests per second per host ----------
_HOST_AT, _HOST_LOCK, _RPS = {}, threading.Lock(), [2.0]


def _throttle(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    with _HOST_LOCK:
        now = time.monotonic()
        at = max(_HOST_AT.get(host, 0.0), now)
        _HOST_AT[host] = at + 1.0 / _RPS[0]
    if at > now:
        time.sleep(at - now)


def _get(url, timeout=20, ua=UA):
    _throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json, text/html, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _install_throttle():
    """creative_sources' board fetchers go through its module-level _get: put the same
    per-host limiter in front of it (idempotent)."""
    import creative_sources as cs
    if not getattr(cs._get, "_polite", False):
        inner = cs._get

        def polite(url, timeout=20):
            _throttle(url)
            return inner(url, timeout)
        polite._polite = True
        cs._get = polite
    return cs


# ---------- names and domains ----------
def _norm(name):
    import sponsors
    return sponsors.norm(name)


def _domain(url):
    host = urllib.parse.urlparse(url if "//" in (url or "") else "//" + (url or "")).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _slugs(c):
    """Board-slug guesses, strongest first: explicit hints, domain label, name joined / hyphenated.
    No first-word-only guess ("apex" for "Apex Media" lands on someone else's board)."""
    words = _norm(c["name"]).split()
    dom = (c.get("domain") or "").split(".")
    label = dom[-2] if len(dom) >= 2 and dom[-2] not in ("co", "com", "org", "net") else dom[0]
    cands = [*c.get("slugs", []), label, "".join(words), "-".join(words)]
    return [s for s in dict.fromkeys(x.lower() for x in cands if x) if re.fullmatch(r"[a-z0-9][a-z0-9-]{2,40}", s)]


# ---------- seed lists ----------
def load_seeds(lists, extra_db=None):
    """[{name, domain, country, slugs, board, src}] merged over `lists`, de-duplicated by
    normalised name; the first list that names a field wins."""
    merged = {}
    for lst in lists:
        rows = (extra_db or []) if lst == "db" else _read_seed(lst)
        for r in rows:
            key = _norm(r.get("name"))
            if not key:
                continue
            m = merged.setdefault(key, {"name": r["name"], "src": [], "slugs": []})
            m["src"].append(lst)
            for f in ("domain", "country", "board"):
                m.setdefault(f, r.get(f))
                if not m[f]:
                    m[f] = r.get(f)
            m["slugs"] += [s for s in r.get("slugs", []) if s not in m["slugs"]]
    return merged


def _read_seed(lst):
    try:
        return json.loads((SEEDS / f"security_{lst}.json").read_text(encoding="utf-8")).get("companies", [])
    except (OSError, ValueError):
        return []


def _write_seed(lst, companies):
    SEEDS.mkdir(parents=True, exist_ok=True)
    out = {"list": lst, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "companies": sorted(companies, key=lambda c: c["name"].lower())}
    (SEEDS / f"security_{lst}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(companies)


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = _norm(r["name"])
        if k and k not in seen and len(k) >= 2:
            seen.add(k)
            out.append(r)
    return out




def fetch_ecso():
    page = _get("https://ecs-org.eu/members/", timeout=60)
    rows = [{"name": html.unescape(n).strip(), "domain": _domain(u)} for n, u in re.findall(
        r'elementor-flip-box__layer__title">\s*(.*?)\s*</p>.*?href="(https?://[^"]+)"', page, re.S)]
    return _dedupe(rows)


_CREST_SKIP = re.compile(r"crest|google|linkedin|twitter|x\.com|facebook|youtube|microsoft|cookiedatabase|w3\.org|gmpg|wordpress")


def fetch_crest(max_pages=80):
    base, rows, seen = "https://marketplace.crest.org", [], set()
    for n in range(1, max_pages + 1):
        page = _get(f"{base}/suppliers/?_paged={n}", timeout=40)
        found = [(html.unescape(t).strip(), s) for s, t in re.findall(
            r'/supplier/([^/"?]+)/[^"]*"[^>]*>\s*View Profile\s*<span[^>]*>\s*for (.*?)\s*</span>', page, re.S)]
        new = [(t, s) for t, s in found if s not in seen]
        if not new:
            break
        for title, s in new:
            seen.add(s)
            row = {"name": title}
            try:
                sup = _get(f"{base}/supplier/{s}/", timeout=40)
                site = next((u for u in re.findall(r'href="(https?://[^"]+)"', sup) if not _CREST_SKIP.search(u)), "")
                row["domain"] = _domain(site)
                m = re.search(r'tax-headquarters tax-item">.*?<span[^>]*>\s*([A-Za-z .,\'-]{2,40})\s*<', sup, re.S)
                row["country"] = m.group(1).strip() if m else ""
            except Exception:
                pass
            rows.append(row)
    return _dedupe(rows)


_PROGRAMME = re.compile(r"\s*[-:|(]*\s*(?:public\s+)?(?:bug\s*bounty|vulnerability\s+disclosure|responsible\s+disclosure|vdp|"
                        r"security\s+programme|bounty)\b.*$", re.I)


def fetch_bounty():
    raw = "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/"
    rows = []
    for fn, name_key, slug_key in (("intigriti_data.json", "name", "company_handle"),
                                   ("yeswehack_data.json", "name", "id"), ("hackerone_data.json", "name", "handle")):
        try:
            data = json.loads(_get(raw + fn, timeout=120))
        except Exception as exc:
            print(f"security_osint: {fn}: {exc}", file=sys.stderr)
            continue
        for p in data:
            name = _PROGRAMME.sub("", p.get(name_key) or "").strip()
            if len(name) < 2:
                continue
            slug = (p.get(slug_key) or "").lower()
            rows.append({"name": name, "domain": _domain(p.get("website") or ""),
                         "slugs": [slug] if fn != "hackerone_data.json" and slug else []})
    return _dedupe(rows)


_CYBER_NAME = re.compile(r"cyber|infosec|pentest|penetration|appsec|devsec|information security|network security|"
                         r"cloud security|application security|security (?:software|labs|research|consult\w*)|"
                         r"threat|vulnerab|secops|ethical hack", re.I)


def fetch_register():
    import sponsors
    rows = []
    for country in ("uk", "nl"):
        rows += [{"name": n, "country": country.upper()} for n, _t in sponsors._rows(country)
                 if _CYBER_NAME.search(n) and not sponsors._NEG.search(n)]
    return _dedupe(rows)


FETCHERS = {"ecso": fetch_ecso, "crest": fetch_crest, "bounty": fetch_bounty,
            "register": fetch_register}


def refresh_seeds(lists=None):
    """Rebuild the fetched seed lists (not `curated`, which is edited by hand)."""
    out = {}
    for lst in lists or FETCHERS:
        try:
            rows = FETCHERS[lst]()
            if rows:   # never replace a shipped list with an empty fetch
                out[lst] = _write_seed(lst, rows)
            else:
                out[lst] = "empty fetch: kept the old list"
        except Exception as exc:
            out[lst] = f"failed ({exc}); kept the old list"
        print(f"security_osint: seed {lst}: {out[lst]}", file=sys.stderr, flush=True)
    return out


def db_companies():
    """Companies jobs.db already holds with a security-persona title, plus their ATS board when a
    stored URL names one: their other postings rarely reach aggregators."""
    import sqlite3
    import sponsors
    rows = {}
    try:
        con = sqlite3.connect(f"file:{(ROOT / 'autopilot/jobs.db').as_posix()}?mode=ro", uri=True, timeout=30)
        boards = sponsors._seen_boards()[0]
        for company, in con.execute(
                "SELECT DISTINCT company FROM jobs WHERE persona='appsec' AND company<>'' AND "
                "(title LIKE '%security%' OR title LIKE '%pentest%' OR title LIKE '%penetration%' OR title LIKE '%infosec%' "
                "OR title LIKE '%cyber%' OR title LIKE '%appsec%' OR title LIKE '%red team%')"):
            rows[company] = {"name": company, "board": boards.get(company)}
        con.close()
    except Exception as exc:
        print(f"security_osint: db list unavailable ({exc})", file=sys.stderr)
    return list(rows.values())


# ---------- board resolution ----------
_ATS_LINKS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)|greenhouse\.io/embed/job_board/js\?for=([\w-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)")),
    ("lever_eu", re.compile(r"jobs\.eu\.lever\.co/([\w-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)")),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com")),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.de")),
    ("personio_com", re.compile(r"([\w-]+)\.jobs\.personio\.com")),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com")),
]
_BAD_SLUG = {"embed", "v1", "api", "j", "jobs", "careers", "www", "static", "assets", "cdn", "app", "apply", "boards",
             "job-boards", "widget", "support", "help", "blog", "docs", "status", "api-eu", "careers-page"}
_PROBE_ORDER = ("greenhouse", "ashby", "lever", "workable", "smartrecruiters", "recruitee", "personio", "teamtailor")


def ats_links(page):
    """[(ats, slug)] named by ATS URLs found in a careers page."""
    out = []
    for ats, rx in _ATS_LINKS:
        for m in rx.finditer(page):
            slug = urllib.parse.unquote(next(g for g in m.groups() if g)).strip("/")
            if slug.lower() not in _BAD_SLUG and (ats, slug) not in out:
                out.append((ats, slug))
    return out


def jsonld_postings(page, base_url):
    """JobPosting objects from <script type=application/ld+json> blocks as board rows."""
    rows = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if o.get("@graph"):
                walk(o["@graph"])
            if "JobPosting" in str(o.get("@type")):
                loc = o.get("jobLocation")
                loc = loc[0] if isinstance(loc, list) and loc else loc
                addr = (loc or {}).get("address") or {} if isinstance(loc, dict) else {}
                place = ", ".join(str(addr.get(k)) for k in ("addressLocality", "addressCountry") if isinstance(addr.get(k), (str, int))
                                  ) if isinstance(addr, dict) else ""
                if o.get("jobLocationType") == "TELECOMMUTE" and not place:
                    place = "Remote"
                rows.append({"title": str(o.get("title") or ""), "url": urllib.parse.urljoin(base_url, o.get("url") or o.get("sameAs") or ""),
                             "location": place, "description": _text(o.get("description")), "company": ""})
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            walk(json.loads(html.unescape(blob.strip())))
        except ValueError:
            pass
    return [r for r in rows if r["title"] and r["url"]]


def _text(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ul|ol|tr)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t\xa0]+", " ", t)).strip()


def _smartrecruiters(slug):
    cs = _install_throttle()
    out, seen = [], set()
    for q in ("security", "penetration", "cyber"):
        d = json.loads(cs._get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&q={q}"))
        for j in d.get("content") or []:
            if j.get("id") in seen:
                continue
            seen.add(j["id"])
            loc = j.get("location") or {}
            out.append(cs._row(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                               ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x)))
    return out


def fetch_board(ats, slug):
    """Postings (title/url/location/description dicts) of one ATS board; raises when it does not exist."""
    cs = _install_throttle()
    if ats == "smartrecruiters":
        return _smartrecruiters(slug)
    if ats == "jsonld":
        return jsonld_postings(_get(slug), slug)
    return cs.FETCHERS[ats](slug)


def resolve(c, cached, today):
    """(board, rows, sure). board is [ats, slug] / ["jsonld", url] / None; sure=False when a
    miss may be a transient failure (retried sooner than a clean 404)."""
    if cached and cached.get("board"):
        try:
            return cached["board"], fetch_board(*cached["board"]), True
        except Exception:
            return cached["board"], [], True
    cands, sure, found_ld = [], True, None
    if c.get("board"):
        cands.append(tuple(c["board"]))
    dom = c.get("domain")
    if dom:
        for path in ("/careers", "/jobs", ""):
            try:
                page = _get(f"https://{dom}{path}", timeout=15)
            except Exception:
                continue
            cands += [x for x in ats_links(page) if x not in cands]
            ld = jsonld_postings(page, f"https://{dom}{path}")
            if ld and not found_ld:
                found_ld = (f"https://{dom}{path}", ld)
            if cands:
                break
    for ats, slug in cands:
        try:
            return [ats, slug], fetch_board(ats, slug), True
        except Exception:
            continue
    if found_ld:
        return ["jsonld", found_ld[0]], found_ld[1], True
    for slug in _slugs(c)[:2]:
        for ats in _PROBE_ORDER:
            try:
                rows = fetch_board(ats, slug)
            except urllib.error.HTTPError as exc:
                sure = sure and exc.code in (404, 410)
                continue
            except Exception:
                sure = False
                continue
            if rows:
                return [ats, slug], rows, True
    return None, [], sure


# ---------- discovery ----------
def _read_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


_US_ONLY = re.compile(r"(?<!\w)(?:U\.?S\.?A?|United States|Canada|Americas|North America)(?!\w)", re.I)
_PRIORITY = {"curated": 0, "crest": 1, "ecso": 2, "register": 3, "wikidata": 4, "bounty": 5, "db": 6}


def discover(cfg, limit, classify, offers_sponsorship=None, needs_no_visa=None, visa_free_location=None,
             companies=None, fetcher=None):
    """Postings of security employers' careers boards for the appsec persona that have a visa
    path. `companies`/`fetcher` replace the seed lists / network (tests)."""
    import sponsors
    conf = {**DEFAULTS, **((cfg or {}).get("discovery") or {}).get("security_osint", {})}
    if not conf["enabled"] or (cfg or {}).get("active_persona") == "creative":
        return []
    _RPS[0] = float(conf["rps"])
    seeds = companies if companies is not None else load_seeds(
        conf["lists"], db_companies() if "db" in conf["lists"] else None)
    cache, today = _read_cache(), date.today()
    retry = int(conf["miss_retry_days"])
    known, fresh = [], []
    for key, c in seeds.items():
        e = cache.get(key) or {}
        if e.get("board"):
            known.append((key, c, e))
        elif not e or (today - date.fromisoformat(e["t"])).days > (retry if e.get("sure", True) else 7):
            fresh.append((key, c, e))
    fresh.sort(key=lambda x: (min(_PRIORITY.get(s, 9) for s in x[1]["src"]), x[1]["name"].lower()))
    todo = fresh[:int(conf["max_companies_per_run"])] + known   # new employers first: they are the point
    fetch = fetcher or (lambda c, e: resolve(c, e, today))

    def run(item):
        try:
            return item, fetch(item[1], item[2])
        except Exception:
            return item, (None, [], False)
    stats = collections.Counter(companies=len(seeds), probed=len(fresh[:int(conf["max_companies_per_run"])]),
                                unprobed=max(0, len(fresh) - int(conf["max_companies_per_run"])))
    jobs, stored_first = [], []
    label = {"uk": "UK Home Office register of licensed sponsors", "nl": "IND register of recognised sponsors"}
    with ThreadPoolExecutor(max_workers=int(conf["workers"])) as ex:
        for n, ((key, c, old), (board, rows, sure)) in enumerate(ex.map(run, todo), 1):
            if n % 100 == 0:
                print(f"security_osint: {n}/{len(todo)} companies, {stats['boards']} boards, {len(jobs)} postings kept",
                      file=sys.stderr, flush=True)
                _write_cache(cache)   # a killed run keeps what it learned
            cache[key] = {"t": today.isoformat(), "board": board, "name": c["name"],
                          **({} if board or sure else {"sure": False}), "n": len(rows)}
            if board:
                stats["boards"] += 1
                stats["new_boards"] += not old.get("board")
            stats["postings"] += len(rows)
            for r in rows:
                persona = classify(r["title"], cfg) if r.get("title") and r.get("url") else None
                if persona != "appsec":
                    continue
                stats["title_fit"] += 1
                job = {"title": r["title"], "company": c["name"], "location": r.get("location") or "", "url": r["url"],
                       "description": r.get("description") or "", "persona": persona}
                why = sponsors.visa_path(job, persona, offers_sponsorship, needs_no_visa, visa_free_location)
                if why and why.startswith("remote worldwide") and _US_ONLY.search(f"{job['title']} {job['location']}"):
                    why = None   # "Remote (Anywhere in the U.S.)" is not worldwide
                if why:
                    stats["visa_path"] += 1
                    job["description"] = f"{job['description']}\n\nVisa path (security_osint): {why}. Board {board[0]}: {board[1]}".strip()
                    job["visa_path"] = why
                    jobs.append(job)
    try:
        _write_cache(cache)
    except OSError as exc:
        print(f"security_osint: cache not saved ({exc})", file=sys.stderr)
    SUMMARY.clear()
    SUMMARY.update(stats)
    print("security_osint: " + ", ".join(f"{k} {v}" for k, v in stats.items()), file=sys.stderr, flush=True)
    return jobs[:limit] if limit else jobs


# ---------- CLI ----------
def _main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh-seeds", action="store_true", help="re-download the fetched seed lists")
    ap.add_argument("--list", nargs="+", choices=list(FETCHERS), help="with --refresh-seeds: only these lists")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--run", action="store_true", help="dry run: print the postings, ingest nothing")
    ap.add_argument("--max-companies", type=int)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    if a.refresh_seeds:
        print(json.dumps(refresh_seeds(a.list), indent=1))
    if a.stats:
        seeds = load_seeds(LISTS[:-1])
        cache = _read_cache()
        print(json.dumps({"seed_lists": {l: len(_read_seed(l)) for l in LISTS[:-1]}, "unique_companies": len(seeds),
                          "cached": len(cache), "boards": sum(1 for e in cache.values() if e.get("board"))}, indent=1))
    if a.run:
        import autopilot as A
        cfg = A.get_persona_cfg(A.read_json(A.CFG), "appsec")
        if a.max_companies:
            cfg.setdefault("discovery", {}).setdefault("security_osint", {})["max_companies_per_run"] = a.max_companies
        for j in discover(cfg, 0, A.classify_persona, A.offers_sponsorship, A.needs_no_visa, A.visa_free_location):
            print(f"{j['company']} | {j['title']} | {j['location']} | {j['visa_path']} | {j['url']}")


if __name__ == "__main__":
    _main()
