"""Remote-first and visa-sponsorship job boards: ONE discover source, `remote_visa_boards`, made of
independent adapters (config discovery.remote_visa_boards.<adapter>.enabled).

  workable_aggregator  jobs.workable.com public JSON API: every Workable-hosted posting, searched by title
  jobgether            jobgether.com public API for agents (/astroapi/ai/jobs.json, allowed by its robots.txt)
  jobscollider         Remote First Jobs public JSON API (documented as free; credit: remotefirstjobs.com)
  visasponsor_jobs     visasponsor.jobs server-rendered search (JSON-LD on the job page, board lists sponsors only)
  workingnomads        Working Nomads exposed_jobs feed (49 curated postings)
  freehire             FreeHire public agent API, full descriptions and original employer URLs

Public pages and open APIs only: no login, no captcha or anti-bot bypass, one request per `gap` seconds
per host. A board that answers 401/403/429 or a Cloudflare challenge is skipped for `blocked_skip_hours`.

`discover(cfg, classify, europe_location, visa_ok)` returns dicts (title, company, location, url, description)
for the persona in cfg["active_persona"]. Rows pass the same gates as every other source: classify (persona
title terms, seniors excluded), age <= max_age_days, no US-only, then creative: Europe/remote, appsec:
`visa_ok` (= sponsors.visa_path). Aggregator-only rows are pointed at the employer's apply page when it can
be found (tools/jsonld_careers.py, tools/employer_url.py) and dropped when it cannot.

Settings: config.json discovery.remote_visa_boards.<adapter>; seeds: autopilot/seeds/remote_visa_queries.json;
cache: autopilot/cache/osint_remote_visa_boards.json. CLI: python tools/remote_visa_boards.py stats
"""
import difflib
import html
import ipaddress
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import creative_sources as CS
import employer_url as EU
import jsonld_careers as J
import osint_sources as O

NAME = "remote_visa_boards"
ADAPTERS = ("workable_aggregator", "jobgether", "jobscollider", "visasponsor_jobs", "workingnomads", "freehire")
COMMON = {"enabled": True, "max_age_days": 14, "gap": 1.5, "max_items": 400, "blocked_skip_hours": 12,
          "details_per_run": 80, "resolve_per_run": 60}
DEFAULTS = {
    "enabled": True,
    "workable_aggregator": {"pages": 2, "gap": 0.6, "searches": [{}, {"location": "Europe"}], "keep_unresolved": True},
    "jobgether": {"pages": 2, "gap": 2.0, "locations": ["europe", "anywhere"],
                  "experience": "entry-level-graduate,junior-1-2-years,mid-level-2-5-years"},
    "jobscollider": {"pages": 2, "gap": 2.0, "categories": {"appsec": ["cybersecurity"], "creative": []}},
    "visasponsor_jobs": {"pages": 2, "gap": 1.5, "countries": ["", "germany", "netherlands", "ireland", "portugal", "united-kingdom"]},
    "workingnomads": {"gap": 3.0},
    "freehire": {"gap": 1.5, "page_size": 50},
}
SUMMARY = {}
_BLOCKED = re.compile(r"just a moment|attention required|cf-mitigated|access denied|captcha", re.I)
_NO_APPLY = re.compile(r"(?:^|\.)(?:dice\.com|forms\.gle|docs\.google\.com|jobgether\.com|remotefirstjobs\.com|jobscollider\.com|"
                       r"workingnomads\.com|visasponsor\.jobs|linkedin\.com|indeed\.[a-z.]+|glassdoor\.[a-z.]+)$", re.I)


def settings(cfg, name=None):
    top = ((cfg or {}).get("discovery") or {}).get(NAME) or {}
    if name is None:
        return {**DEFAULTS, **{k: v for k, v in top.items() if not isinstance(v, dict)}}
    return {**COMMON, **DEFAULTS.get(name, {}), **(top.get(name) or {})}


# ---------- plumbing ----------
def _get(url, headers=None, gap=1.5, timeout=30):
    return O._get(url, None, headers, timeout, gap)


def _final_url(url, gap=3.0, timeout=20):
    """Where a redirecting apply link ends up (headers only are needed, the body is never read)."""
    J._wait(urllib.parse.urlparse(url).netloc.lower())
    req = urllib.request.Request(url, headers={"User-Agent": O.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl()


def _cache():
    c = O._load(NAME)
    return {k: c.get(k) or {} for k in ("detail", "employer", "blocked", "health")}


def _store(c):
    keep = lambda d, days: {k: v for k, v in d.items() if O._age_days(v.get("t")) < days}
    O._save(NAME, {"detail": keep(c["detail"], 14), "employer": keep(c["employer"], 30),
                   "blocked": keep(c["blocked"], 3), "health": keep(c["health"], 30)})


def _text(raw, cap=6000):
    return CS._text(raw)[:cap]


def _day(v):
    """date of an ISO / DD-MM-YYYY / RFC 2822 stamp, or None."""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", s)   # 12-10-2026 (visasponsor.jobs, day first)
    d = J._date(f"{m[1]}.{m[2]}.{m[3]}" if m else s)
    if d:
        return d
    try:
        return datetime.strptime(s[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
    except ValueError:
        return None


def _fresh(posted, max_age, today=None):
    d = _day(posted)
    return d is None or (today or date.today()) - d <= timedelta(days=max_age)


def _row(title, company, location, url, desc="", posted="", **extra):
    return {"title": (title or "").strip(), "company": (company or "").strip(), "location": (location or "").strip(),
            "url": url or "", "description": desc or "", "posted": str(posted or ""), **extra}


def _queries(cfg, persona, s):
    """Seed queries, then ASCII title terms of the persona (config personas.<p>.search.required_title_terms);
    discovery.remote_visa_boards.<adapter>.queries.<persona> replaces, .extra_queries.<persona> adds."""
    base = (s.get("queries") or {}).get(persona)
    if base is None:
        base = list(O._seed("remote_visa_queries.json").get(persona, []))
        terms = [t for t in ((cfg or {}).get("search") or {}).get("required_title_terms") or []
                 if re.fullmatch(r"[a-z0-9 ]{4,24}", t) and len(t.split()) <= 3 and t not in ("soc", "iam", "photo")]
        base += terms
    base += (s.get("extra_queries") or {}).get(persona, [])
    return list(dict.fromkeys(base))[: int(s.get("max_queries", 14))]


# ---------- workable_aggregator ----------
def run_workable_aggregator(cfg, s, persona, classify):
    rows, seen = [], set()

    def one(job):
        q, extra = job
        out, token = [], None
        for _ in range(int(s["pages"])):
            params = {"query": q, "day_range": s["max_age_days"], **extra, **({"pageToken": token} if token else {})}
            d = json.loads(_get("https://jobs.workable.com/api/v1/jobs?" + urllib.parse.urlencode(params), gap=s["gap"]))
            page = d.get("jobs") or []
            out += page
            token = d.get("nextPageToken")
            if not token or not any(classify(j.get("title") or "") == persona for j in page):
                break   # results run out of the persona's titles: deeper pages are noise
        return out
    for jobs in _pmap_safe(one, [(q, e) for q in _queries(cfg, persona, s) for e in s["searches"]], name="workable_aggregator"):
        for j in jobs:
            if j.get("id") in seen or j.get("state") not in (None, "published"):
                continue
            seen.add(j.get("id"))
            loc = j.get("location") or {}
            remote = j.get("workplace") == "remote" or "TELECOMMUTE" in (j.get("locations") or [])
            place = ", ".join(x for x in (loc.get("city"), loc.get("countryName")) if x)
            c = j.get("company") or {}
            desc = _text("\n".join([j.get("description") or "", j.get("requirementsSection") or "", j.get("benefitsSection") or ""]))
            rows.append(_row(j.get("title"), c.get("title"), ("Remote, " if remote else "") + place if place or remote else "",
                             j.get("url"), desc, j.get("created"), website=c.get("website") or "", workplace=j.get("workplace") or ""))
    return rows


def resolve_workable_aggregator(row, s, classify):
    """apply.workable.com/<account>/j/<code> of the same title, when the account slug can be guessed
    (company name or website label); else the jobs.workable.com page, which has its own apply form."""
    host = urllib.parse.urlparse(row.get("website") or "").netloc.lower().removeprefix("www.")
    slugs = list(dict.fromkeys(x for x in (re.sub(r"[^a-z0-9]+", "-", row["company"].lower()).strip("-"),
                                           host.split(".")[0] if host else "") if x))
    want = EU._norm_title(row["title"])
    for slug in slugs[:2]:
        try:
            d = json.loads(_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", gap=2.0))
        except Exception:
            continue
        for j in d.get("jobs") or []:
            if EU._norm_title(j.get("title")) == want and (j.get("url") or j.get("shortlink")):
                return j.get("url") or j.get("shortlink")
    return ""


# ---------- jobgether ----------
def run_jobgether(cfg, s, persona, classify):
    rows, seen = [], set()

    def one(job):
        q, loc = job
        out = []
        for page in range(1, int(s["pages"]) + 1):
            params = {"keyword": q, "locations": loc, "experience": s["experience"], "sort": "date", "limit": 25, "page": page}
            d = json.loads(_get("https://jobgether.com/astroapi/ai/jobs.json?" + urllib.parse.urlencode(params), gap=s["gap"]))
            out += d.get("jobs") or []
            if not (d.get("pagination") or {}).get("hasMore") or not any(classify(j.get("title") or "") == persona for j in d.get("jobs") or []):
                break
        return out
    for jobs in _pmap_safe(one, [(q, loc) for q in _queries(cfg, persona, s) for loc in s["locations"]], name="jobgether"):
        for j in jobs:
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            rows.append(_row(j.get("title"), j.get("company"), f"{j.get('remote') or 'Remote'}, {j.get('location') or ''}".strip(", "),
                             j.get("url"), "", j.get("postedAt"), uid=j.get("id")))
    return rows


def enrich_jobgether(row, s):
    """Offer page: description (JSON-LD) and the employer's own ATS link (utm parameters stripped)."""
    h = _get(row["url"], gap=s["gap"])
    posts = J.parse_jobpostings(h, row["url"])
    ats = ""
    for u in EU.urls_in(h):
        if EU.classify_url(u) == "ats" and EU.is_job_url(u):
            p = urllib.parse.urlsplit(u)
            ats = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
            break
    return {"description": _text(posts[0].get("description")) if posts else "", "apply": ats}


# ---------- jobscollider (Remote First Jobs) ----------
def run_jobscollider(cfg, s, persona, classify):
    rows, seen = [], set()

    def one(job):
        kind, val = job
        out = []
        for page in range(int(s["pages"])):
            d = json.loads(_get("https://jobscollider.com/api/search-jobs?" + urllib.parse.urlencode({kind: val, "page": page}), gap=s["gap"]))
            out += d.get("jobs") or []
            if len(d.get("jobs") or []) < 100:
                break
        return out
    tasks = [("query", q) for q in _queries(cfg, persona, s)] + [("category", c) for c in (s["categories"] or {}).get(persona, [])]
    for jobs in _pmap_safe(one, tasks, name="jobscollider"):
        for j in jobs:
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            where = ", ".join(x for x in j.get("locations") or [] if x)
            rows.append(_row(j.get("title"), j.get("company_name"), "Remote, " + where if where else "Remote", j.get("url"),
                             _text(j.get("description")) + "\n\nSource: Remote First Jobs (remotefirstjobs.com)", j.get("published_at"),
                             uid=j.get("id")))
    return rows


def resolve_site(row, classify, site):
    """Apply URL of the same title found on the employer's own site / embedded ATS board, else ''."""
    try:
        res = J.probe_site(site, classify, fetch_cap=3, budget_s=30)
    except Exception:
        return ""
    want, best, score = EU._norm_title(row["title"]), "", 0.0
    for p in res.get("postings") or []:
        sc = difflib.SequenceMatcher(None, EU._norm_title(p["title"]), want).ratio()
        if sc > score:
            best, score = p.get("apply_url") or p.get("url") or "", sc
    return best if score >= 0.88 else ""


def site_of(row, s):
    """Employer website from the JSON-LD (hiringOrganization.sameAs) of the Remote First Jobs page."""
    h = _get(row["url"], gap=s["gap"])
    m = re.search(r'"sameAs"\s*:\s*"(https?://[^"]+)"', h)
    return m.group(1) if m else ""


# ---------- visasponsor.jobs ----------
_CARD = re.compile(r'<a class="job-card" href="(/job/[^"]+)">(.*?)</a>', re.S)


def parse_visasponsor_cards(page):
    out = []
    for href, body in _CARD.findall(page):
        g = lambda cls: _text((re.search(rf'<[a-z0-9]+ class="{cls}"[^>]*>(.*?)</[a-z0-9]+>', body, re.S) or [None, ""])[1])
        visas = [v for v in re.findall(r'class="pill pill-visa">(.*?)</span>', body) if "unspecified" not in v.lower()]
        out.append(_row(g("job-title"), g("job-company"), g("job-location"), "https://visasponsor.jobs" + href,
                        posted=(re.search(r"(\d{2}-\d{2}-\d{4})", body) or [None, ""])[1], visas=[html.unescape(v) for v in visas]))
    return out


def run_visasponsor_jobs(cfg, s, persona, classify):
    rows, seen = [], set()

    def one(job):
        q, country = job
        out = []
        for page in range(1, int(s["pages"]) + 1):
            path = f"/jobs/{country}-country" if country else "/jobs"
            h = _get(f"https://visasponsor.jobs{path}?" + urllib.parse.urlencode({"keywords": q, "page": page}), gap=s["gap"])
            cards = parse_visasponsor_cards(h)
            out += cards
            if len(cards) < 30 or not any(classify(c["title"]) == persona for c in cards):
                break
        return out
    for cards in _pmap_safe(one, [(q, c) for q in _queries(cfg, persona, s) for c in s["countries"]], name="visasponsor_jobs"):
        for r in cards:
            if r["url"] not in seen:
                seen.add(r["url"])
                rows.append(r)
    return rows


def enrich_visasponsor_jobs(row, s):
    h = _get(row["url"], gap=s["gap"])
    posts = J.parse_jobpostings(h, row["url"])
    body = re.search(r"Job Description:?\s*(.*?)\s*(?:Apply Now)", _text(h, 40000), re.S)
    ext = [u for u in re.findall(r'href="(https?://[^"]+)"', h) if "visasponsor.jobs" not in u and not re.search(r"\.(?:png|jpe?g|svg|webp)(?:\?|$)", u)]
    desc = _text(posts[0].get("description")) if posts and posts[0].get("description") else (body.group(1) if body else "")
    visas = ", ".join(row.get("visas") or [])   # the board lists sponsoring employers only: say so, the visa gate reads the text
    desc += "\nVisa sponsorship offered (listed on visasponsor.jobs" + (f": {visas}" if visas else "") + ")."
    return {"description": desc, "apply": next((u for u in ext if not _NO_APPLY.search(urllib.parse.urlparse(u).netloc)), "")}


# ---------- workingnomads ----------
def run_workingnomads(cfg, s, persona, classify):
    out = []
    for j in json.loads(_get("https://www.workingnomads.com/api/exposed_jobs/", gap=s["gap"])):
        out.append(_row(j.get("title"), j.get("company_name"), j.get("location") or "Remote", j.get("url"),
                        _text(j.get("description")), j.get("pub_date"), tags=j.get("tags") or ""))
    return out


def resolve_workingnomads(row, s):
    return _final_url(row["url"], gap=s["gap"])


# ---------- freehire (documented public API; no CV or account data sent) ----------
_COUNTRY_NAMES = {
    "gb": "United Kingdom", "de": "Germany", "fr": "France", "es": "Spain", "it": "Italy", "pt": "Portugal",
    "nl": "Netherlands", "be": "Belgium", "at": "Austria", "ch": "Switzerland", "ie": "Ireland", "se": "Sweden",
    "no": "Norway", "dk": "Denmark", "fi": "Finland", "is": "Iceland", "pl": "Poland", "cz": "Czech Republic",
    "sk": "Slovakia", "hu": "Hungary", "ro": "Romania", "bg": "Bulgaria", "gr": "Greece", "hr": "Croatia",
    "si": "Slovenia", "ee": "Estonia", "lv": "Latvia", "lt": "Lithuania", "mt": "Malta", "lu": "Luxembourg",
    "cy": "Cyprus", "rs": "Serbia", "am": "Armenia", "ge": "Georgia", "kz": "Kazakhstan", "tr": "Turkey",
    "me": "Montenegro", "ae": "United Arab Emirates", "us": "United States", "ca": "Canada",
}


def _freehire_location(job):
    place = (job.get("location") or "").strip()
    # shortcut: unlisted ISO codes stay as codes; extend when a new market needs text-based matching.
    countries = list(dict.fromkeys(_COUNTRY_NAMES.get(str(c).lower(), str(c).upper()) for c in job.get("countries") or []))
    # Keep country evidence in the stored location: downstream jobs do not retain API facets.
    if not place:
        place = ", ".join(countries)
    elif len(countries) == 1 and not any(re.search(r"\b" + re.escape(c) + r"\b", place, re.I) for c in _COUNTRY_NAMES.values()):
        place += ", " + countries[0]
    if job.get("work_mode") == "remote":
        place = "Remote, " + place if place else "Remote"
    return place


def run_freehire(cfg, s, persona, classify):
    def one(q):
        # FreeHire's unquoted words are ORed; quoted words must all match the title.
        params = {"q": '"' + q.replace('"', '') + '"', "q_fields": "title", "limit": max(1, min(100, int(s["page_size"]))),
                  "description_format": "text", "posted_within_days": s["max_age_days"],
                  "sort": "posted_at", "order": "desc"}
        data = json.loads(_get("https://freehire.me/api/v1/agent/jobs/search?" + urllib.parse.urlencode(params),
                               headers={"User-Agent": "madnessbrainsbl/applypilot (+https://github.com/madnessbrainsbl/applypilot)"},
                               gap=s["gap"]))
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("FreeHire: invalid search response")
        if (data.get("meta") or {}).get("ignored_params"):
            raise ValueError("FreeHire: search filters were ignored")
        rows = []
        for j in data["data"]:
            if j.get("closed_at") or j.get("requires_clearance"):
                continue
            url = j.get("url") or ""
            loc = _freehire_location(j)
            rows.append(_row(j.get("title"), j.get("company"), loc, url,
                             _text(j.get("description"), 40000), j.get("posted_at"), apply=url))
        return rows

    return [r for rows in _pmap_safe(one, _queries(cfg, persona, s), name="freehire") for r in rows]


RUNNERS = {"workable_aggregator": run_workable_aggregator, "jobgether": run_jobgether, "jobscollider": run_jobscollider,
           "visasponsor_jobs": run_visasponsor_jobs, "workingnomads": run_workingnomads, "freehire": run_freehire}
ENRICH = {"jobgether": enrich_jobgether, "visasponsor_jobs": enrich_visasponsor_jobs}


def _pmap_safe(fn, items, workers=4, name=None):
    """Keep successful queries, count failures, and prefer the access error when every query fails."""
    err, out = [], []

    def one(i):
        try:
            return fn(i)
        except Exception as exc:
            err.append(exc)
            return []
    out = O._pmap(one, items, workers)
    if err and name:
        st = SUMMARY.setdefault(name, {"errors": 0})
        st["errors"] += len(err)
        if any(_is_wall(e) for e in err):
            st["blocked_reason"] = "upstream access/rate limit"
    if err and not any(out):
        raise next((e for e in err if _is_wall(e)), err[0])
    for e in err:
        print(f"{NAME}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return out


def _is_wall(exc):
    return getattr(exc, "code", 0) in (401, 403, 429) or bool(_BLOCKED.search(str(exc)))


# ---------- pipeline ----------
def _gate(rows, persona, classify, europe_location, visa_ok, s):
    """(cheap-filtered rows, gate) : title/age/US-only first; the persona gate runs after enrichment."""
    out, seen = [], set()
    for r in rows:
        key = (r["company"].lower(), r["title"].lower(), r["location"].lower())
        if (not r["title"] or not r["url"] or r["url"] in seen or key in seen or classify(r["title"]) != persona
                or not _fresh(r["posted"], int(s["max_age_days"])) or O._US_ONLY.search(r["location"])):
            continue
        seen.update((r["url"], key))
        out.append(r)

    def passes(r):
        if persona == "creative" and europe_location:
            if r["location"] and not (europe_location({"location": r["location"]}) or CS._REMOTE_RE.search(r["location"])):
                return False
        return visa_ok({k: r.get(k) or "" for k in ("title", "company", "location", "description", "url")}, persona) if visa_ok else True
    return out, passes


def run_adapter(name, cfg, persona, classify, europe_location, visa_ok, cache):
    s = settings(cfg, name)
    st = {"raw": 0, "kept": 0, "employer": 0, "dropped_unresolved": 0, "errors": 0}
    SUMMARY[name] = st
    if not s["enabled"]:
        st["skipped"] = "disabled"
        return []
    if O._age_days(cache["blocked"].get(name, {}).get("t")) < float(s["blocked_skip_hours"]) / 24:
        st["skipped"] = "blocked: " + cache["blocked"][name].get("why", "")
        return []
    try:
        rows = RUNNERS[name](cfg, s, persona, classify)
    except Exception as exc:
        st["errors"] = max(1, st["errors"])
        print(f"{NAME}: {name}: {exc}", file=sys.stderr, flush=True)
        if _is_wall(exc):
            cache["blocked"][name] = {"t": O._now().isoformat(), "why": str(exc)[:80]}
        return []
    st["raw"] = len(rows)
    rows, passes = _gate(rows, persona, classify, europe_location, visa_ok, s)
    if name in ENRICH:   # listing without a description: read the page of the rows that fit, newest first
        for r in sorted(rows, key=lambda r: str(r["posted"]), reverse=True)[: int(s["details_per_run"])]:
            e = cache["detail"].get(r["url"])
            if not e:
                try:
                    e = {**ENRICH[name](r, s), "t": O._now().isoformat()}
                    cache["detail"][r["url"]] = e
                except Exception as exc:
                    st["errors"] += 1
                    if _is_wall(exc):
                        st["blocked_reason"] = "upstream access/rate limit"
                    print(f"{NAME}: {name}: {r['url']}: {exc}", file=sys.stderr, flush=True)
                    continue
            r["description"] = r["description"] or e.get("description", "")
            r["apply"] = e.get("apply", "")
        rows = [r for r in rows if r["description"]]
    kept = [r for r in rows if passes(r)]
    kept = _resolve(name, kept, s, classify, cache, st)
    for r in kept:
        note = [f"Posted: {r['posted'][:10]}"] if r["posted"] else []
        r["description"] = "\n".join([r["description"], *note, f"Source: {name}"]).strip()
        r["src"] = name
    kept = kept[: int(s["max_items"])]
    st["kept"] = len(kept)
    return kept


def _public_apply_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").rstrip(".")
        if p.scheme not in ("http", "https") or "." not in host or p.username is not None or p.password is not None or p.port == 0:
            return False
        if host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def _resolve(name, rows, s, classify, cache, st):
    """Point rows at the employer's apply page; rows that only have an aggregator page are dropped
    (unless the adapter's own page applies, keep_unresolved)."""
    budget, out = [int(s["resolve_per_run"])], []
    for r in rows:
        listing = r["url"]
        target = r.get("apply") or ""
        hit = cache["employer"].get(listing)
        if hit is not None and (hit.get("url") or O._age_days(hit.get("t")) < 3):
            target = target or hit.get("url", "")
        elif not target and budget[0] > 0:
            budget[0] -= 1
            try:
                if name == "workable_aggregator":
                    target = resolve_workable_aggregator(r, s, classify)
                elif name == "workingnomads":
                    target = resolve_workingnomads(r, s)
                elif name == "jobscollider":
                    site = site_of(r, s)
                    target = resolve_site(r, classify, site) if site else ""
            except Exception as exc:
                st["errors"] += 1
                if _is_wall(exc):
                    st["blocked_reason"] = "upstream access/rate limit"
                print(f"{NAME}: {name}: resolve {listing}: {exc}", file=sys.stderr, flush=True)
            cache["employer"][listing] = {"url": target, "t": O._now().isoformat()}
        if _public_apply_url(target) and not _NO_APPLY.search(urllib.parse.urlparse(target).netloc) and EU.classify_url(target) != "aggregator":
            r["url"], r["listing_url"] = target, listing
            r["description"] += f"\n\nListing: {listing}"
            st["employer"] += 1
            out.append(r)
        elif s.get("keep_unresolved"):
            out.append(r)
        else:
            st["dropped_unresolved"] += 1
    return out


def discover(cfg, classify, europe_location, visa_ok=None, adapters=None):
    """Postings of cfg["active_persona"] from every enabled adapter (see module doc)."""
    SUMMARY.clear()
    if not settings(cfg).get("enabled", True):
        return []
    persona, cache, out, seen = (cfg or {}).get("active_persona"), _cache(), [], set()
    for name in adapters or ADAPTERS:
        rows = run_adapter(name, cfg or {}, persona, classify, europe_location, visa_ok, cache)
        st = SUMMARY[name]
        if st.get("blocked_reason"):
            cache["blocked"][name] = {"t": O._now().isoformat(), "why": st["blocked_reason"]}
        elif not st["errors"] and not st.get("skipped"):
            cache["blocked"].pop(name, None)
        status = ("disabled" if st.get("skipped") == "disabled" else "cooldown" if st.get("skipped") else
                  "partial" if st["errors"] and st["raw"] else "error" if st["errors"] else "ok")
        blocked = cache["blocked"].get(name)
        retry_at = ((datetime.fromisoformat(blocked["t"]) + timedelta(hours=float(settings(cfg, name)["blocked_skip_hours"]))).isoformat()
                    if blocked else None)
        st.update(status=status, t=O._now().isoformat(), retry_at=retry_at)
        cache["health"][f"{persona}:{name}"] = dict(st)
        for r in rows:
            if r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
    _store(cache)
    return out


def adapter_health(cfg):
    """Last observed adapter state, read-only; absence of evidence is never 'ok'."""
    cache, out = _cache(), []
    for key, st in sorted(cache["health"].items()):
        persona, name = key.split(":", 1)
        enabled = settings(cfg).get("enabled", True) and settings(cfg, name)["enabled"]
        out.append({**st, "adapter": name, "persona": persona,
                    "status": st["status"] if enabled else "disabled"})
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["stats"]:
        c = _cache()
        print(json.dumps({"detail": len(c["detail"]), "employer": len(c["employer"]), "blocked": c["blocked"]}, indent=1))
