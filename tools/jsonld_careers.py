"""Hidden-vacancy crawler: company career pages read through schema.org JobPosting JSON-LD.

For a list of company sites (autopilot/cache/jsonld_seeds.json, built from Wikidata) it
  1. reads robots.txt (a Disallow for us ends the visit) and the home page,
  2. finds the careers page (home links, then /careers /jobs /karriere ...),
  3. detects embedded ATS boards (greenhouse, lever, ashby, workable, personio, recruitee,
     teamtailor, smartrecruiters, bamboohr, breezy) and reads their public APIs,
  4. opens job detail pages whose link text / slug fits a persona (also from the sitemap)
     and parses their JSON-LD JobPosting (title, organisation, location, datePosted,
     validThrough, description).
Public pages only: no login, no captcha handling, <= 2 requests/s per host.

`discover_postings(...)` is the `jsonld_careers` discover source; results of every site are
cached in autopilot/cache/jsonld_careers.json so a rerun only revisits sites that are due.
Config (autopilot/config.json): discovery.jsonld_careers = {enabled, max_sites_per_run, host_gap_s, workers,
max_age_days}. Seeds: autopilot/seeds/jsonld_*.json ([{site, name, country, via}]).
CLI: python tools/jsonld_careers.py stats | seed <file.json>   (the crawl itself runs as `discover --source jsonld_careers`)"""
import gzip
import html
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "autopilot/cache"
SEEDS = ROOT / "autopilot/seeds"
STORE = CACHE / "jsonld_careers.json"
UA = "Mozilla/5.0 (compatible; JobSearchBot/1.0; personal job search; public pages only)"
MAX_AGE_DAYS = 14          # datePosted older than this is not ingested
REVISIT_DAYS = 7           # a site with a careers page / ATS board is read again after this
MISS_REVISIT_DAYS = 30     # a site without any is retried after this
MAX_BYTES = 1_000_000
SITE_BUDGET_S = 90         # stop reading a site after this long (the proxy answers in ~6 s per request)

# ---------------------------------------------------------------- polite HTTP
_HOST_LOCK, _HOST_NEXT = threading.Lock(), {}
HOST_GAP = 0.5   # seconds between requests to one host (<= 2 req/s)


def _wait(host):
    with _HOST_LOCK:
        now = time.monotonic()
        at = max(now, _HOST_NEXT.get(host, 0.0))
        _HOST_NEXT[host] = at + HOST_GAP
    if at > now:
        time.sleep(at - now)


def get(url, timeout=10, throttle=True):
    """(text, final_url) or None. Never raises."""
    try:
        if throttle:
            _wait(urllib.parse.urlparse(url).netloc.lower())
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip",
                                                   "Accept": "text/html,application/json,application/xml,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES)
            if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return raw.decode("utf-8", "ignore"), r.geturl()
    except Exception:
        return None


def get_json(url, timeout=15):
    r = get(url, timeout)
    if not r:
        return None
    try:
        return json.loads(r[0])
    except ValueError:
        return None


# ---------------------------------------------------------------- JSON-LD
_LD_RE = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_ISO2 = {"DE": "Germany", "FR": "France", "ES": "Spain", "PT": "Portugal", "IT": "Italy", "NL": "Netherlands",
         "BE": "Belgium", "LU": "Luxembourg", "AT": "Austria", "CH": "Switzerland", "IE": "Ireland",
         "GB": "United Kingdom", "UK": "United Kingdom", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
         "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czech Republic", "SK": "Slovakia",
         "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "HR": "Croatia", "SI": "Slovenia",
         "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "MT": "Malta", "CY": "Cyprus", "RS": "Serbia",
         "GE": "Georgia", "AM": "Armenia", "AE": "United Arab Emirates", "KZ": "Kazakhstan", "US": "United States",
         "CA": "Canada", "AU": "Australia", "IN": "India", "TR": "Turkey", "UA": "Ukraine"}


def _list(x):
    return x if isinstance(x, list) else ([] if x is None else [x])


def _name(x):
    if isinstance(x, dict):
        return str(x.get("name") or x.get("legalName") or "").strip()
    return str(x or "").strip()


def _country(x):
    n = _name(x)
    return _ISO2.get(n.upper(), n) if len(n) <= 3 else n


def _place(loc):
    if isinstance(loc, str):
        return loc.strip()
    if not isinstance(loc, dict):
        return ""
    a = loc.get("address", loc)
    if isinstance(a, str):
        return a.strip()
    if not isinstance(a, dict):
        return ""
    parts = [str(a.get("addressLocality") or "").strip(), str(a.get("addressRegion") or "").strip(),
             _country(a.get("addressCountry"))]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _text(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ul|ol|tr)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t\xa0]+", " ", t)).strip()


def _date(v):
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"\s*(\d{1,2})[./](\d{1,2})[./](\d{4})", str(v or ""))   # 12.10.2026 (day first)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _walk(node, out, depth=0):
    if depth > 6:
        return
    if isinstance(node, list):
        for n in node:
            _walk(n, out, depth + 1)
    elif isinstance(node, dict):
        t = node.get("@type")
        if "JobPosting" in (t if isinstance(t, list) else [t]):
            out.append(node)
        for k in ("@graph", "itemListElement", "item", "mainEntity", "mainEntityOfPage", "hasPart"):
            if k in node:
                _walk(node[k], out, depth + 1)


def parse_jobpostings(page_html, page_url=""):
    """schema.org JobPosting objects in a page's JSON-LD -> list of posting dicts."""
    found = []
    for raw in _LD_RE.findall(page_html or ""):
        raw = re.sub(r"^\s*(?:<!--|//<!\[CDATA\[|<!\[CDATA\[)|(?:-->|//\]\]>|\]\]>)\s*$", "", raw.strip())
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            continue
        _walk(data, found)
    out = []
    for j in found:
        title = _text(str(j.get("title") or j.get("name") or ""))
        if not title:
            continue
        places = [p for p in (_place(l) for l in _list(j.get("jobLocation"))) if p]
        remote = "TELECOMMUTE" in str(j.get("jobLocationType") or "").upper()
        if remote:
            req = [_country(r) for r in _list(j.get("applicantLocationRequirements"))]
            places.append("Remote" + (" - " + ", ".join(r for r in req if r) if any(req) else ""))
        url = j.get("url") if isinstance(j.get("url"), str) and j["url"].startswith("http") else ""
        out.append({"title": title,
                    "company": _name(j.get("hiringOrganization")),
                    "location": "; ".join(dict.fromkeys(places)),
                    "posted": str(j.get("datePosted") or "")[:10],
                    "valid_through": str(j.get("validThrough") or "")[:10],
                    "description": _text(str(j.get("description") or "")),
                    "url": page_url or url, "apply_url": url,
                    "employment": ",".join(str(x) for x in _list(j.get("employmentType")))})
    return out


def is_fresh(p, today=None, max_age=None):
    """False when datePosted is older than `max_age` days or validThrough has passed."""
    today, max_age = today or date.today(), max_age or MAX_AGE_DAYS
    posted, until = _date(p.get("posted")), _date(p.get("valid_through"))
    if until and until < today:
        return False
    return not (posted and (today - posted).days > max_age)


# ---------------------------------------------------------------- page analysis
_HREF_RE = re.compile(r"<a\s[^>]*?href=[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_CAREER_RE = re.compile(
    r"career|karriere|\bjobs?\b|/jobs?|stellen|emploi|recrut|vacature|werken[-_ ]?bij|lavora|trabaja|empleo|offres?|"
    r"join[-_ ]?us|work[-_ ](?:with|for|at)[-_ ]us|opportunit|vacanc|openings|hiring|arbeiten[-_ ]bei|bewerb|"
    r"rekryt|ledige|stilling|praca|kariera|karri[eä]r|ansök|offerte[-_ ]di[-_ ]lavoro|werk\b", re.I)
_NOT_CAREER = re.compile(r"mailto:|tel:|javascript:|\.(?:pdf|jpe?g|png|gif|svg|zip)(?:\?|$)|/blog/|/news/|/press/|"
                         r"facebook\.com|twitter\.com|instagram\.com|linkedin\.com/(?:company|in)|youtube\.com", re.I)
_JOBLINK_RE = re.compile(r"job|career|vacanc|position|opening|posting|stelle|karriere|emploi|offre|offer|vacature|"
                         r"apply|opportunit|lavoro|empleo|/p/|/o/|/j/|\d{4,}", re.I)
FALLBACK_PATHS = ("/careers", "/jobs", "/karriere", "/career", "/en/careers", "/company/careers", "/about/careers",
                  "/join-us", "/work-with-us", "/vacatures", "/emplois", "/stellenangebote")

# (ats, regex) - first group is the board slug
_ATS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed)([\w-]+)", re.I)),
    ("lever_eu", re.compile(r"jobs\.eu\.lever\.co/([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!api|j/)([\w-]+)", re.I)),
    ("personio", re.compile(r"(?<![\w-])([\w-]{1,60})\.jobs\.personio\.de", re.I)),
    ("personio_com", re.compile(r"(?<![\w-])([\w-]{1,60})\.jobs\.personio\.com", re.I)),
    ("recruitee", re.compile(r"(?<![\w-])([\w-]{1,60})\.recruitee\.com", re.I)),
    ("teamtailor", re.compile(r"(?<![\w-])([\w-]{1,60})\.teamtailor\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)|api\.smartrecruiters\.com/v1/companies/([\w-]+)", re.I)),
    ("bamboohr", re.compile(r"(?<![\w-])([\w-]{1,60})\.bamboohr\.com", re.I)),
    ("breezy", re.compile(r"(?<![\w-])([\w-]{1,60})\.breezy\.hr", re.I)),
]
_ATS_HOST = {"greenhouse": "greenhouse.io", "lever": "lever.co", "lever_eu": "lever.co", "ashby": "ashbyhq.com",
             "workable": "workable.com", "personio": "personio.de", "personio_com": "personio.com", "recruitee": "recruitee.com",
             "teamtailor": "teamtailor.com", "smartrecruiters": "smartrecruiters.com", "bamboohr": "bamboohr.com", "breezy": "breezy.hr"}
_ATS_SKIP = {"www", "api", "app", "static", "assets", "cdn", "careers", "career", "jobs", "embed", "boards", "support", "help",
             "developers", "docs", "blog", "status", "j", "company", "login", "my"}
# boards read as an ordinary listing page (links -> detail pages -> JSON-LD)
_LISTING_HOSTS = re.compile(r"https?://(?:join\.com/companies/[\w-]+|[\w-]{1,60}\.(?:softgarden\.io|rexx-systems\.com|"
                            r"onlyfy\.jobs|d-vinci\.jobs|jobs\.cornerstoneondemand\.com|pinpointhq\.com|homerun\.co|"
                            r"gohire\.io|jobylon\.com|careerpuck\.com|comeet\.com))", re.I)


def find_ats(page_html):
    """[(ats, slug)] of the ATS boards a page links to or embeds."""
    out, low = [], (page_html or "").lower()
    for ats, rx in _ATS:
        if _ATS_HOST[ats] not in low:
            continue
        for m in rx.finditer(page_html or ""):
            slug = next((g for g in m.groups() if g), "")
            if slug and slug.lower() not in _ATS_SKIP and (ats, slug) not in out:
                out.append((ats, slug))
    return out


def find_listing_hosts(page_html):
    return list(dict.fromkeys(m.group(0) for m in _LISTING_HOSTS.finditer(page_html or "")))


def links(page_html, base):
    """[(absolute url, anchor text)] of a page."""
    out = []
    for href, inner in _HREF_RE.findall(page_html or ""):
        href = html.unescape(href).strip()
        if _NOT_CAREER.search(href):
            continue
        try:
            out.append((urllib.parse.urljoin(base, href), _text(inner)[:160]))
        except ValueError:
            continue
    return out


def _site_key(host):
    return re.sub(r"^www\.", "", host.lower())


_CAREER_PATH = re.compile(r"^/(?:[a-z]{2}(?:[-_][a-z]{2})?/)?(?:[\w-]*(?:career|karriere|jobs?|stellen|emploi|recrut|vacature|werken|lavora|"
                          r"trabaja|empleo|join|hiring|vacanc|opportunit|rekryt|praca|kariera)[\w-]*)(?:/[\w-]+)?/?$", re.I)
_CAREER_TEXT = re.compile(r"(?:careers?|jobs?|job openings?|jobangebote|stellenangebote|stellenmarkt|karriere|emplois?|offres d.emploi|recrutement|"
                          r"nous rejoindre|rejoignez.*|vacatures?|werken bij.*|join (?:us|our team|the team)|we.re hiring|hiring|"
                          r"work (?:with|for|at) us|open positions?|openings|vacancies|trabaja con nosotros|empleo|lavora con noi|"
                          r"offerte di lavoro|kariera|praca|karri[eä]r)", re.I)
_NOT_PAGE = re.compile(r"login|signin|sign-in|anmeld|account|konto|abo|subscri|event|politic|shop|store|podcast|newsletter|press|blog|news", re.I)


def same_site(url, base):
    """url is on the site `base` (or its careers./jobs. sibling) or on a known ATS host."""
    host, h = _site_key(urllib.parse.urlparse(base).netloc), _site_key(urllib.parse.urlparse(url).netloc)
    return (h == host or h.endswith("." + host) or h.partition(".")[::2] in (("careers", host), ("jobs", host))
            or bool(find_ats(url)) or bool(find_listing_hosts(url)))


def career_links(page_html, base):
    """Same-site links that look like a careers page, best first (short anchor text, short path)."""
    cands = set()
    for url, text in links(page_html, base):
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or _NOT_PAGE.search(p.netloc + p.path) or not same_site(url, base):
            continue
        by_text = bool(_CAREER_TEXT.fullmatch(text.strip(" .!>›»→")))
        if by_text or _CAREER_PATH.match(p.path) or p.netloc.split(".")[0] in ("careers", "jobs", "karriere"):
            cands.add((not by_text, len(p.path), url))
    return [u for *_, u in sorted(cands)][:3]


def detail_links(page_html, base, classify):
    """Links on a listing page whose text/slug fits a persona -> [(url, persona)]."""
    out, seen = [], set()
    for url, text in links(page_html, base):
        path = urllib.parse.urlparse(url).path
        slug = re.sub(r"[-_/+.]|%20", " ", path)
        if not _JOBLINK_RE.search(path) or url in seen or not same_site(url, base):
            continue
        persona = classify(text) or classify(slug)
        if persona:
            seen.add(url)
            out.append((url, persona))
    return out[:10]


def sitemap_urls(text):
    return [html.unescape(u).strip() for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", text or "", re.S)]


_SM_JOB = re.compile(r"job|career|vacanc|stellen|karriere|position|opening|emploi|offre|vacature|recruit", re.I)


# ---------------------------------------------------------------- ATS boards
_API_HOST = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "lever_eu": "api.eu.lever.co",
             "ashby": "api.ashbyhq.com", "workable": "apply.workable.com", "smartrecruiters": "api.smartrecruiters.com"}


def _ats_rows(ats, slug, classify):
    """Postings of a public ATS board whose title fits a persona -> [posting dict]."""
    import creative_sources as C
    rows = []
    if ats in _API_HOST:
        _wait(_API_HOST[ats])   # the shared API hosts get the same <= 2 req/s as company sites
    try:
        if ats == "smartrecruiters":
            for q in ("security", "pentest", "photographer", "video", "content creator"):
                d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&q={q.replace(' ', '%20')}") or {}
                for j in d.get("content") or []:
                    loc = j.get("location") or {}
                    rows.append(C._row(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                                       ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x),
                                       "", (j.get("company") or {}).get("name") or ""))
                    rows[-1]["posted"] = str(j.get("releasedDate") or "")[:10]
        elif ats == "bamboohr":
            d = get_json(f"https://{slug}.bamboohr.com/careers/list") or {}
            for j in d.get("result") or []:
                loc = j.get("location") or {}
                rows.append(C._row(j.get("jobOpeningName"), f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
                                   ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)))
        elif ats == "breezy":
            for j in get_json(f"https://{slug}.breezy.hr/json") or []:
                loc = j.get("location") or {}
                rows.append(C._row(j.get("name"), j.get("url"), loc.get("name") or "", "", (j.get("company") or {}).get("name") or ""))
                rows[-1]["posted"] = str(j.get("published_date") or "")[:10]
        elif ats in C.FETCHERS:
            rows = C.FETCHERS[ats](slug)
    except Exception:
        return []
    out = []
    for r in rows:
        persona = classify(r["title"]) if r.get("title") and r.get("url") else None
        if persona:
            out.append({"title": r["title"], "company": r.get("company") or slug.replace("-", " ").title(),
                        "location": r.get("location") or "", "posted": r.get("posted", ""), "valid_through": "",
                        "description": r.get("description") or "", "url": r["url"], "apply_url": r["url"],
                        "employment": "", "src": f"ats:{ats}:{slug}"})
    return out


# ---------------------------------------------------------------- one site
def _robots(base, fetch=None):
    """(RobotFileParser, [sitemap urls]); a missing robots.txt allows everything."""
    rp = urllib.robotparser.RobotFileParser()
    r = (fetch or get)(base + "/robots.txt")
    if not r or "<html" in r[0][:300].lower():
        return None, []
    rp.parse(r[0].splitlines())
    return rp, [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r[0])]


def probe_site(site, classify, fetch_cap=5, budget_s=None):
    """Read one company site. Returns {'host','status','careers','ats','n_ld','postings'}.
    `classify(text)` -> persona name or None."""
    t_end = time.monotonic() + (budget_s or SITE_BUDGET_S)
    fetch = lambda url, timeout=10: get(url, timeout) if time.monotonic() < t_end else None   # slow proxy: cap the time per site
    p = urllib.parse.urlparse(site if "//" in site else "https://" + site)
    base = f"{p.scheme or 'https'}://{p.netloc}"
    res = {"host": _site_key(p.netloc), "status": "ok", "careers": [], "ats": [], "n_ld": 0, "postings": []}
    rp, sitemaps = _robots(base, fetch)
    allowed = (lambda u: rp.can_fetch(UA, u) or rp.can_fetch("*", u)) if rp else (lambda u: True)
    if not allowed(base + "/"):
        res["status"] = "robots"
        return res
    home = fetch(base + "/")
    if not home:
        res["status"] = "no_home"
        return res
    home_html, home_url = home
    base = f"{urllib.parse.urlparse(home_url).scheme}://{urllib.parse.urlparse(home_url).netloc}"   # after redirects
    ats = list(find_ats(home_html))
    listing_urls = find_listing_hosts(home_html)
    pages = [(home_html, home_url)]
    cl = [u for u in career_links(home_html, home_url) if allowed(u)]
    for u in (cl[:2] or [base + x for x in FALLBACK_PATHS[:2]]):
        r = fetch(u)
        # a guessed path must really be a careers URL: many sites answer any path with the home page
        if r and (cl or _CAREER_RE.search(urllib.parse.urlparse(r[1]).path)) and r[1] not in res["careers"]:
            res["careers"].append(r[1])
            pages.append(r)
            ats += [a for a in find_ats(r[0]) if a not in ats]
            listing_urls += [x for x in find_listing_hosts(r[0]) if x not in listing_urls]
            if not cl:
                break
    ats += [a for a in find_ats(" ".join(cl + res["careers"])) if a not in ats]   # the careers link itself may be a board
    res["ats"] = [list(a) for a in ats]
    posts, fetched = [], set()

    def add_ld(url, h):
        found = parse_jobpostings(h, url)
        res["n_ld"] += len(found)
        for q in found:
            if is_fresh(q) and classify(q["title"]):
                posts.append({**q, "src": "jsonld"})

    for h, u in pages:
        add_ld(u, h)
    details = []
    for h, u in pages[1:]:
        details += [d for d in detail_links(h, u, classify) if d not in details]
    for lu in listing_urls[:2]:
        r = fetch(lu)
        if r:
            details += [d for d in detail_links(r[0], r[1], classify)]
    if res["careers"] and not posts and not details and not ats:
        # a careers page exists but lists nothing we can read (script-rendered): sitemap job URLs whose slug fits a persona
        for sm in sitemaps[:1]:
            r = fetch(sm) if allowed(sm) else None
            if not r:
                continue
            locs = sitemap_urls(r[0])
            if locs and all(l.endswith((".xml", ".xml.gz")) for l in locs[:5]):
                jobmaps = [l for l in locs if _SM_JOB.search(l)][:1]
                locs = [x for m in jobmaps for x in sitemap_urls((fetch(m) or ("",))[0])]
            for l in locs:
                slug = re.sub(r"[-_/+.]|%20", " ", urllib.parse.urlparse(l).path)
                if _SM_JOB.search(l) and classify(slug):
                    details.append((l, classify(slug)))
    for url, _persona in details:
        if url in fetched or len(fetched) >= fetch_cap or not allowed(url):
            continue
        fetched.add(url)
        r = fetch(url)
        if r:
            add_ld(r[1], r[0])
    for a, slug in ats[:3]:
        posts += _ats_rows(a, slug, classify)
    res["postings"] = list({q["url"]: q for q in posts}.values())
    return res


# ---------------------------------------------------------------- store + source
_STORE_LOCK = threading.Lock()
_LAST_CRAWL = {}


def load_store():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sites": {}}


def save_store(store):
    with _STORE_LOCK:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STORE)


def load_seeds(folder=None):
    """Every autopilot/seeds/*.json merged, one entry per host, best tier first."""
    out = {}
    for f in sorted((folder or SEEDS).glob("jsonld_*.json")):
        try:
            for s in json.loads(f.read_text(encoding="utf-8")):
                out.setdefault(_site_key(urllib.parse.urlparse(s["site"]).netloc), s)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    # best tier first ("prio": 1 = employers of both personas ... 5 = long tail), mixed within a tier
    return sorted(out.values(), key=lambda s: (s.get("prio", 5), zlib.crc32(s["site"].encode())))


def due(entry, today=None):
    """True when a site has never been read or its re-visit time has come."""
    if not entry:
        return True
    today = today or date.today()
    days = REVISIT_DAYS if (entry.get("careers") or entry.get("ats")) else MISS_REVISIT_DAYS
    return today - date.fromisoformat(entry["t"]) >= timedelta(days=days)


def crawl(classify, max_sites=500, workers=16, seeds=None, store=None, log=None, save_every=100, budget_s=None):
    """Probe up to `max_sites` due sites in parallel; returns the updated store."""
    store = store if store is not None else load_store()
    seeds = seeds if seeds is not None else load_seeds()
    todo = [s for s in seeds if due(store["sites"].get(_site_key(urllib.parse.urlparse(s["site"]).netloc)))][:max_sites]
    t0, done = time.monotonic(), 0

    def run(s):
        if budget_s and time.monotonic() - t0 > budget_s:
            return s, None
        try:
            return s, probe_site(s["site"], classify)
        except Exception as exc:
            return s, {"host": _site_key(urllib.parse.urlparse(s["site"]).netloc), "status": f"error:{str(exc)[:60]}",
                       "careers": [], "ats": [], "n_ld": 0, "postings": []}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, r in ex.map(run, todo):
            if r is None:
                continue
            for q in r["postings"]:
                q["company"] = q.get("company") or s.get("name", "")
            store["sites"][r["host"]] = {"t": date.today().isoformat(), "name": s.get("name", ""), "country": s.get("country", ""),
                                         "status": r["status"], "careers": r["careers"], "ats": r["ats"], "n_ld": r["n_ld"],
                                         "postings": r["postings"]}
            done += 1
            if done % save_every == 0:
                save_store(store)
                if log:
                    log(f"{done}/{len(todo)} sites, {sum(len(v['postings']) for v in store['sites'].values())} postings")
    save_store(store)
    return store


def discover_postings(cfg, limit, classify_persona, offers_sponsorship=None, needs_no_visa=None,
                      visa_free_location=None, europe_location=None, per_run=None, workers=16):
    """The `jsonld_careers` source: crawl the sites that are due (discovery.jsonld_careers.max_sites_per_run,
    default 300), then return every cached posting that is fresh, fits a persona and has a visa
    path (creative: Europe / remote)."""
    import sponsors
    global HOST_GAP, MAX_AGE_DAYS
    s_cfg = ((cfg or {}).get("discovery") or {}).get("jsonld_careers") or {}
    if s_cfg.get("enabled", True) is False:
        return []
    HOST_GAP = float(s_cfg.get("host_gap_s", HOST_GAP))
    MAX_AGE_DAYS = int(s_cfg.get("max_age_days", MAX_AGE_DAYS))
    per_run = int(per_run or s_cfg.get("max_sites_per_run", 300))
    workers = int(s_cfg.get("workers", workers))
    classify = lambda text: classify_persona(text, cfg)
    if time.monotonic() - _LAST_CRAWL.get("t", -1e9) > 600:   # discover asks once per persona: crawl once per run
        store = crawl(classify, per_run, workers, log=lambda m: print(f"jsonld_careers: {m}", file=sys.stderr, flush=True))
        _LAST_CRAWL["t"] = time.monotonic()
    else:
        store = load_store()
    out, today = [], date.today()
    for host, e in store["sites"].items():
        age = (today - date.fromisoformat(e["t"])).days
        for q in e.get("postings") or []:
            if not is_fresh(q, today) or (not q.get("posted") and age > 5):
                continue
            persona = classify(q["title"])
            if not persona or not q.get("url"):
                continue
            job = {"title": q["title"], "company": q.get("company") or e.get("name") or host, "url": q["url"],
                   "location": q.get("location") or "", "persona": persona,
                   "description": (q.get("description") or "") + (f"\nPosted: {q['posted']}" if q.get("posted") else "")}
            if persona == "creative" and europe_location:
                ok = (not job["location"]) or europe_location(job) or re.search(r"remote|worldwide|anywhere", job["location"], re.I)
            else:
                ok = sponsors.visa_path(job, persona, offers_sponsorship, needs_no_visa, visa_free_location, cfg=cfg)
            if ok:
                out.append(job)
    seeds_n = len(load_seeds())
    print(f"jsonld_careers: {len(store['sites'])} of {seeds_n} sites read, {sum(1 for e in store['sites'].values() if e.get('careers') or e.get('ats'))} "
          f"with a careers page/ATS -> {len(out)} postings that fit a persona", file=sys.stderr, flush=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    if sys.argv[1:2] == ["stats"]:
        st = load_store()["sites"]
        print(len(st), "sites;", sum(1 for e in st.values() if e.get("careers")), "careers pages;",
              sum(1 for e in st.values() if e.get("ats")), "with ATS;", sum(len(e.get("postings") or []) for e in st.values()), "postings;",
              len(load_seeds()), "seeds")
    elif sys.argv[1:2] == ["seed"] and len(sys.argv) > 2:   # add a [{site,name,country}] file to autopilot/seeds/
        import shutil
        SEEDS.mkdir(parents=True, exist_ok=True)
        shutil.copy(sys.argv[2], SEEDS / Path(sys.argv[2]).name)
    else:
        sys.exit("usage: jsonld_careers.py stats | seed <file.json>   (crawl: autopilot.py discover --source jsonld_careers)")
