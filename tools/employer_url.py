"""Employer application target of aggregator listings (arbeitsagentur, eu_portals).

The aggregator detail page has no application form. `resolve_listing` finds where to apply:
  ats    a concrete job on greenhouse/lever/ashby/workable/smartrecruiters/pinpoint/personio/recruitee/
         teamtailor/join.com/bamboohr (also found embedded in the employer page, jsonld_careers.find_ats)
  page   the employer's own posting page (generic form filler)
  email  only an e-mail address -> row status 'manual_contact', never queued
  none   nothing usable (row stays as it is)
Ingest: `apply_to_rows` (called by osint_sources.discover). Existing rows:
  python tools/employer_url.py backfill [--limit N] [--dry-run] [--workers 4]
  python tools/employer_url.py contacts
"""
import argparse
import base64
import difflib
import html
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonld_careers as J
import osint_sources as O

LISTING_RE = {"arbeitsagentur": "https://www.arbeitsagentur.de/jobsuche/jobdetail/",
              "eu_portals": "https://europa.eu/eures/portal/jv-se/jv-details/"}
ATS_HOST = re.compile(r"(?:^|\.)(?:greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|pinpointhq\.com|"
                      r"personio\.(?:de|com)|recruitee\.com|teamtailor\.com|join\.com|bamboohr\.com)$")
_SUBDOMAIN_ATS = re.compile(r"(?:personio|recruitee|teamtailor|bamboohr|pinpointhq)\.")   # slug lives in the host
AGGREGATOR = re.compile(r"(?:^|\.)(?:arbeitsagentur\.de|europa\.eu|xing\.com|linkedin\.com|indeed\.[a-z.]+|stepstone\.[a-z.]+|"
                        r"finest-jobs\.com|jobexport\.de|jobware\.de|kimeta\.de|jooble\.[a-z.]+|adzuna\.[a-z.]+|meinestadt\.de|monster\.[a-z.]+|"
                        r"glassdoor\.[a-z.]+|jobs\.de|stellenanzeigen\.de|jobrapido\.[a-z.]+|facebook\.com|google\.[a-z.]+|"
                        r"jobbörse\.de|jobboerse\.de|bundesagentur\.de|jobijoba\.[a-z.]+|talent\.com|jobtensor\.com|get-in-it\.de|"
                        r"get-in-engineering\.de|jobscout24\.ch|jobs\.ch|empfehlungsbund\.de|arbetsformedlingen\.se|editx\.eu|"
                        r"nationalevacaturebank\.nl|nederlandvacature\.nl|vacaturesitewonen\.nl|vacatures\.co|arbeidsplassen\.nav\.no|"
                        r"talentus\.be|ictjob\.be|applybe\.com|ago\.jobs|heyjobs\.co|hogapage\.de|freelancer\.nl|helixjobs\.com|"
                        r"carrierebijgt\.nl|feinaactiva\.gencat\.cat|werkenvoornederland\.nl|karriere-suedniedersachsen\.de|ibgids\.nl|"
                        r"freehire\.me|whatjobs\.com|startup\.jobs|hiring\.cafe|hiringcafe\.com)$")
_ROOTISH = {"", "jobs", "careers", "career", "en", "de", "nl", "fr", "job", "o", "companies", "careers-home"}
_URL_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+", re.I)
_MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MAIL_NOISE = re.compile(r"^(?:noreply|no-reply|donotreply|datenschutz|privacy|support)@|@(?:example|arbeitsagentur|bundesagentur)\.", re.I)
GONE = {"kind": "none", "url": "", "emails": []}
RETRY_DAYS = 3   # a listing that resolved to nothing is looked at again after this


def _host(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def classify_url(url):
    """'ats' | 'aggregator' | 'page' of an absolute URL."""
    host = _host(url or "")
    if not host:
        return "aggregator"
    if ATS_HOST.search(host) or "gh_jid=" in url.lower():   # gh_jid: an employer site wrapping a Greenhouse board
        return "ats"
    return "aggregator" if AGGREGATOR.search(host) else "page"


def is_job_url(url):
    """An ATS URL naming one job, not just a company board root."""
    p = urllib.parse.urlparse(url)
    if "gh_jid=" in url.lower():
        return True
    host = p.netloc.lower()
    if host.startswith(("api.", "boards-api.")) or "/embed/job_board" in p.path:   # board widgets, not a job
        return False
    meaningful = [x for x in p.path.split("/") if x and x.lower() not in _ROOTISH]
    return len(meaningful) >= (1 if _SUBDOMAIN_ATS.search(host) else 2)


def urls_in(text):
    return [u.rstrip(".,;:") for u in _URL_RE.findall(html.unescape(text or ""))]


def emails_in(text):
    return list(dict.fromkeys(m for m in _MAIL_RE.findall(html.unescape(text or "")) if not _MAIL_NOISE.search(m)))


def candidates_ba(ext, desc):
    """([(url, trusted)], [emails]) from Arbeitsagentur's externeURL and the description text."""
    urls = [(ext, True)] if ext else []
    for u in urls_in(desc):
        path = urllib.parse.urlparse(u).path
        if u != ext and (classify_url(u) == "ats" or (classify_url(u) == "page" and J._CAREER_RE.search(path))):
            urls.append((u, False))
    return urls, emails_in(desc)


def candidates_eures(detail):
    """([(url, trusted)], [emails]) from a EURES jv detail JSON: web profiles, links in the application
    instructions, e-mail addresses of the contacts."""
    urls, mails = [], []
    for prof in (detail.get("jvProfiles") or {}).values():
        for c in prof.get("personContacts") or []:
            comm = c.get("communications") or {}
            urls += [(w.get("uri"), True) for w in comm.get("webProfiles") or [] if w.get("uri")]
            mails += [e.get("uri") for e in comm.get("emails") or [] if e.get("uri")]
        instr = " ".join(str(x) for x in prof.get("applicationInstructions") or [])
        urls += [(u, True) for u in urls_in(instr)]
        mails += emails_in(instr)
    urls = list(dict.fromkeys(urls))
    return urls, list(dict.fromkeys(m for m in mails if _MAIL_RE.fullmatch(m) and not _MAIL_NOISE.search(m)))


def _norm_title(t):
    t = re.sub(r"[\(\[].*?[\)\]]", " ", (t or "").lower())
    t = re.sub(r"\b[mwfdxi](?:\s*[/|]\s*[mwfdxi])+\b|\balle geschlechter\b", " ", t)
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def _job_ats_link(text):
    for u in urls_in(text):
        if classify_url(u) == "ats" and is_job_url(u):
            return u
    return ""


def _board_match(text_and_url, title, board):
    """Concrete job URL on the ATS board an employer page embeds, picked by title."""
    want = _norm_title(title)
    for ats, slug in J.find_ats(text_and_url)[:2]:
        best, score = "", 0.0
        for r in board(ats, slug, lambda t: True) or []:
            have = _norm_title(r["title"])
            sc = 1.0 if have == want else difflib.SequenceMatcher(None, have, want).ratio()
            if sc > score:
                best, score = r["url"], sc
        if score >= 0.85:
            return best
    return ""


def resolve(urls, emails, title, fetch=J.get, board=J._ats_rows):
    """{"kind": ats|page|email|none, "url": str, "emails": [...]} for candidate URLs (url, trusted) and e-mails."""
    page = ""
    for u, trusted in urls[:4]:
        kind = classify_url(u)
        if kind == "ats" and is_job_url(u):
            return {"kind": "ats", "url": u, "emails": emails}
        r = fetch(u)
        if not r:
            continue
        text, final = r
        if classify_url(final) == "ats" and is_job_url(final):
            return {"kind": "ats", "url": final, "emails": emails}
        hit = _job_ats_link(text) or _board_match(final + "\n" + text, title, board)
        if hit:
            return {"kind": "ats", "url": hit, "emails": emails}
        if trusted and not page and classify_url(final) == "page":
            page = final
    if page:
        return {"kind": "page", "url": page, "emails": emails}
    return {"kind": "email" if emails else "none", "url": "", "emails": emails}


# ---------- listing -> candidates (network) ----------
def resolve_listing(name, listing_url, title, desc, get=O._get):
    ref = listing_url.rsplit("/", 1)[-1]
    if name == "arbeitsagentur":
        ext = (O._load("arbeitsagentur").get(ref) or {}).get("ext")
        if ext is None:
            try:
                dd = json.loads(get(f"{O.BA}/v4/jobdetails/{base64.b64encode(ref.encode()).decode()}", headers=O.BA_KEY, gap=0.4))
                ext = dd.get("externeURL") or ""
            except Exception as exc:
                return GONE if getattr(exc, "code", 0) in (404, 410) else None   # expired listing: do not ask again
        urls, mails = candidates_ba(ext, desc)
    else:
        try:
            detail = json.loads(get(f"{O.EURES}/jv/id/{ref}", headers={"Accept": "application/json"}, gap=0.8))
        except Exception as exc:
            return GONE if getattr(exc, "code", 0) in (404, 410) else None
        urls, mails = candidates_eures(detail)
        mails = list(dict.fromkeys(mails + emails_in(desc)))
    return resolve(urls, mails, title)


def _fresh(e):
    return bool(e) and (e["kind"] != "none" or O._age_days(e.get("t")) < RETRY_DAYS)


def resolve_many(items, workers=4, per_run=None):
    """items: [(name, listing_url, title, desc)] -> {listing_url: entry}; cached in osint_resolve.json."""
    cache = O._load("resolve")
    todo = [i for i in items if not _fresh(cache.get(i[1]))]
    todo = todo[:per_run] if per_run else todo
    for it, res in zip(todo, O._pmap(lambda i: resolve_listing(*i), todo, workers)):
        if res:
            cache[it[1]] = {**res, "t": O._now().isoformat()}
    O._save("resolve", {k: v for k, v in cache.items() if O._age_days(v.get("t")) < 60})
    return {i[1]: cache[i[1]] for i in items if i[1] in cache}


def annotate(listing_url, desc, e):
    """description with the provenance / contact line added once."""
    add = ""
    if e["kind"] in ("ats", "page") and "Listing: " not in desc:
        add = f"Listing: {listing_url}"
    elif e["kind"] == "email" and "Apply by e-mail" not in desc:
        add = "Apply by e-mail: " + ", ".join(e["emails"])
    return desc + (f"\n\n{add}" if add else "")


def apply_to_rows(rows, name, per_run=200):
    """Ingest hook: rows of an aggregator source get the employer URL (listing URL kept in the description),
    or status 'manual_contact' when only an e-mail address exists."""
    res = resolve_many([(name, r["url"], r.get("title", ""), r.get("description", "")) for r in rows
                        if r["url"].startswith(LISTING_RE[name])], per_run=per_run)
    stats = {"ats": 0, "page": 0, "email": 0, "none": 0}
    for r in rows:
        e = res.get(r["url"])
        if not e:
            continue
        stats[e["kind"]] += 1
        r["description"] = annotate(r["url"], r.get("description", ""), e)
        if e["kind"] in ("ats", "page"):
            r["listing_url"], r["url"] = r["url"], e["url"]
        elif e["kind"] == "email":
            r["status"] = "manual_contact"
    O.SUMMARY.setdefault(name, {})["employer_url"] = stats
    return rows


# ---------- CLI ----------
def backfill(args):
    import autopilot as A
    import db
    con = db.connect()
    like = " OR ".join("url LIKE ?" for _ in LISTING_RE)
    rows = con.execute(f"SELECT id, title, url, description FROM jobs WHERE status='queued' AND ({like})",
                       [v + "%" for v in LISTING_RE.values()]).fetchall()
    rows = rows[: args.limit] if args.limit else rows
    name_of = lambda u: next(n for n, p in LISTING_RE.items() if u.startswith(p))
    res = resolve_many([(name_of(r["url"]), r["url"], r["title"], r["description"] or "") for r in rows], args.workers)
    counts = {"ats": 0, "page": 0, "email": 0, "none": 0, "unreachable": 0, "duplicate": 0}
    hosts = {}
    for r in rows:
        e = res.get(r["url"])
        if not e:
            counts["unreachable"] += 1
            continue
        counts[e["kind"]] += 1
        desc = annotate(r["url"], r["description"] or "", e)
        if args.dry_run or e["kind"] == "none":
            continue
        if e["kind"] == "email":
            con.execute("UPDATE jobs SET status='manual_contact', description=? WHERE id=?", (desc, r["id"]))
            continue
        hosts[_host(e["url"])] = hosts.get(_host(e["url"]), 0) + 1
        new_id = A.sha(A.canonical_url(e["url"]))
        if db.job_exists(con, new_id) and new_id != r["id"]:
            counts["duplicate"] += 1
            con.execute("UPDATE jobs SET hidden=1, description=? WHERE id=?", (desc, r["id"]))
        elif any(con.execute(f"SELECT 1 FROM {t} WHERE job_id=?", (r["id"],)).fetchone()
                 for t in ("applications", "outcomes", "outcome_details", "leads")):
            con.execute("UPDATE jobs SET url=?, description=? WHERE id=?", (e["url"], desc, r["id"]))
        else:
            con.execute("UPDATE jobs SET id=?, url=?, description=? WHERE id=?", (new_id, e["url"], desc, r["id"]))
    con.commit()
    print(json.dumps({"rows": len(rows), **counts, "target_hosts": dict(sorted(hosts.items(), key=lambda x: -x[1])[:25])}, indent=1))


def contacts(args):
    import db
    con = db.connect()
    for r in con.execute("SELECT company, title, location, description FROM jobs WHERE status='manual_contact' ORDER BY company"):
        m = re.search(r"Apply by e-mail: (.+)", r["description"] or "")
        print(f"{r['company']} | {r['title']} | {r['location']} | {m.group(1) if m else ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("backfill")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(func=backfill)
    sp.add_parser("contacts").set_defaults(func=contacts)
    a = ap.parse_args()
    a.func(a)
