"""European creative-job discovery (photographer / videographer / content creator /
photo & video editor / retoucher): public ATS boards of media, fashion, e-commerce and
creative employers plus a few public feeds. No login, no browser, polite (one GET per
board, 16 threads).

`discover(cfg, classify, fetch_text)` returns autopilot job dicts and fills `SUMMARY`
with {source: postings kept}. A board is probed with its own ATS API; a slug that does
not exist just returns nothing."""
import json
import re
import html
import urllib.request
try:
    import defusedxml.ElementTree as ET   # feeds are third-party XML
except ImportError:   # pragma: no cover
    import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# (ats, slug) pairs verified live on 2026-10-16 (see CREATIVE_BOARDS below); extend freely,
# a dead slug costs one failed GET.
CREATIVE_BOARDS = {
    "ashby": ["alan", "ankorstore", "artsy", "backmarket", "bynder", "deliveroo", "doctolib", "edited", "epidemic-sound", "flink", "focus", "kinetic", "ledger", "miro", "mollie", "mubi", "nelly", "notion", "paradox", "photoroom", "pika", "polarsteps", "qonto", "sanity", "supercell", "synthesia", "toogoodtogo", "trainline", "webflow", "wistia"],
    "greenhouse": ["action", "adyen", "airbnb", "akqa", "algolia", "autoscout24", "bird", "bolcom", "businessoffashion", "contentful", "deliveroo", "descript", "discord", "dmgmedia", "doctolib", "elsevier", "epicgames", "figma", "flaconi", "flix", "future", "getyourguide", "glossier", "grey", "gymshark", "hearst", "hellofresh", "hugeinc", "immoscout24", "iris", "jdsports", "lightricks", "mirakl", "monks", "monzo", "n26", "numa", "ogilvy", "onrunning", "picsart", "pinterest", "reddit", "remedy", "rga", "roller", "sidlee", "squarespace", "storyblok", "suitsupply", "sumup", "thehutgroup", "tide", "tripadvisor", "trivago", "twitch", "typeform", "voxmedia", "webflow", "wise", "wk", "wolt", "wpp"],
    "lever": ["blablacar", "cheerz", "cloudinary", "contentsquare", "farfetch", "gettyimages", "hypebeast", "moonpig", "mulberry", "qonto", "rai", "spotify", "swile", "veepee", "vestiairecollective", "wgsn", "zeiss", "zeta"],
    "lever_eu": ["jacquemus", "olx"],
    "personio": ["bbdo", "pixum", "scholz-friends", "vaude", "westwing"],
    "recruitee": ["bauer", "bbc", "bonial", "bunq", "dpgmedia", "fcb", "framestore", "google", "jobtome", "lvmh", "massimodutti", "meta", "nikon", "oysho", "prismic", "redbull", "rtl", "srf", "stradivarius", "talpanetwork", "tbwa", "weekday", "zara"],
    "smartrecruiters": ["allegro", "asics", "asos", "bigcommerce", "boconcept", "bytedance", "canva", "coolblue", "curzon", "darty", "galerieslafayette", "gousto", "hmgroup", "home24", "hubertburdamedia", "jobteaser", "jysk", "kiabi", "lorealgroup", "lvmh", "lyst", "omio", "paradox", "picnic", "picsart", "reachplc", "redbull", "salomon", "springernature", "uber", "wayfair", "wehkamp", "wise"],
    "teamtailor": ["aller", "allermedia", "bauermedia", "celio", "civitatis", "deezer", "dotdigital", "egmont", "etam", "ganni", "highsnobiety", "house", "jdsports", "jules", "kinetic", "lagardere", "made", "monster", "nent", "oatly", "payfit", "peakperformance", "picnic", "polestar", "schibsted", "sellpy", "serviceplan", "sigma", "storytel", "swile", "tv2", "undiz", "welcometothejungle"],
}

SUMMARY = {}
_REMOTE_RE = re.compile(r"\b(?:remote|worldwide|anywhere|hybrid)\b", re.I)


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _text(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ul|ol|tr)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t\xa0]+", " ", t)).strip()


def _row(title, url, location, desc="", company=""):
    return {"title": (title or "").strip(), "url": url or "", "location": location or "",
            "description": desc or "", "company": company}


def _greenhouse(slug):
    d = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"))
    return [_row(j.get("title"), j.get("absolute_url"), (j.get("location") or {}).get("name"), _text(j.get("content")))
            for j in d.get("jobs") or []]


def _lever(slug, host="api.lever.co"):
    d = json.loads(_get(f"https://{host}/v0/postings/{slug}?mode=json"))
    return [_row(j.get("text"), j.get("hostedUrl"), (j.get("categories") or {}).get("location"),
                 j.get("descriptionPlain") or _text(j.get("description"))) for j in d if isinstance(j, dict)]


def _lever_eu(slug):
    return _lever(slug, "api.eu.lever.co")


def _ashby(slug):
    d = json.loads(_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}"))
    return [_row(j.get("title"), j.get("jobUrl"), j.get("location"),
                 (j.get("descriptionPlain") or "") or _text(j.get("descriptionHtml"))) for j in d.get("jobs") or []]


_SR_QUERIES = ("photographer", "photography", "video", "content creator", "fotograf", "photographe")


def _smartrecruiters(slug):
    # big boards (H&M 1800, Red Bull 1200 postings): the list API is paged, so search it
    out, seen = [], set()
    for q in _SR_QUERIES:
        d = json.loads(_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&q={q.replace(' ', '%20')}"))
        for j in d.get("content") or []:
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            loc = j.get("location") or {}
            out.append(_row(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                            ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x),
                            company=(j.get("company") or {}).get("name") or ""))
    return out


def _workable(slug):
    d = json.loads(_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"))
    return [_row(j.get("title"), j.get("url") or j.get("shortlink"),
                 ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                 _text(j.get("description")), d.get("name") or "") for j in d.get("jobs") or []]


def _recruitee(slug):
    d = json.loads(_get(f"https://{slug}.recruitee.com/api/offers/"))
    return [_row(j.get("title"), j.get("careers_url"), j.get("location") or j.get("city"),
                 _text(j.get("description")), j.get("company_name") or "") for j in d.get("offers") or []]


def _personio(slug, tld="de"):
    root = ET.fromstring(_get(f"https://{slug}.jobs.personio.{tld}/xml"))
    out = []
    for p in root.iter("position"):
        desc = " ".join(_text(v.findtext("value")) for v in p.iter("jobDescription"))
        out.append(_row(p.findtext("name"), f"https://{slug}.jobs.personio.{tld}/job/{p.findtext('id')}",
                        p.findtext("office"), desc, p.findtext("subcompany") or ""))
    return out


def _personio_com(slug):
    return _personio(slug, "com")


def _teamtailor(slug):
    root = ET.fromstring(_get(f"https://{slug}.teamtailor.com/jobs.rss"))
    return [_row(i.findtext("title"), i.findtext("link"), i.findtext("{*}locations/{*}location/{*}city") or
                 i.findtext("{*}location") or "", _text(i.findtext("description"))) for i in root.iter("item")]


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "lever_eu": _lever_eu, "ashby": _ashby,
            "smartrecruiters": _smartrecruiters, "workable": _workable, "recruitee": _recruitee,
            "personio": _personio, "personio_com": _personio_com, "teamtailor": _teamtailor}


def _board(args):
    ats, slug = args
    try:
        rows = FETCHERS[ats](slug)
    except Exception:
        return ats, slug, None
    name = slug.replace("-", " ").title()
    for r in rows:
        r["company"] = r["company"] or name
    return ats, slug, rows


# ---- feeds -------------------------------------------------------------------------
def _arbeitnow(fetch_text, pages=6):
    out = []
    for n in range(1, pages + 1):
        try:
            data = json.loads(fetch_text(f"https://arbeitnow.com/api/job-board-api?page={n}", timeout=20)).get("data") or []
        except Exception:
            break
        out += [_row(j.get("title"), j.get("url"), f"{j.get('location') or ''}, Germany", _text(j.get("description")), j.get("company_name"))
                for j in data]
    return out


def _creativeboom(fetch_text, pages=6):
    out, seen = [], set()
    for n in range(1, pages + 1):
        try:
            h = fetch_text("https://www.creativeboom.com/jobs/" + (f"page-{n}/" if n > 1 else ""), timeout=20)
        except Exception:
            break
        for m in re.finditer(r'<a [^>]*href="(/jobs/[^"/]+/)"[^>]*>\s*(.*?)\s*</a>', h, re.S):
            body = _text(m.group(2))
            mm = re.match(r"(.+?)\s+at\s+(.+?)\s+(?:Full-time|Part-time|Contract|Freelance|Internship)\b.*?·\s*(?:On-site|Hybrid|Remote)\s*·\s*([^£$€\n]*)", body.replace("\n", " "), re.S)
            if m.group(1) in seen or not mm:
                continue
            seen.add(m.group(1))
            out.append(_row(mm.group(1), "https://www.creativeboom.com" + m.group(1), mm.group(3).strip(), body, mm.group(2)))
    return out


def _jobicy(fetch_text):
    try:
        d = json.loads(fetch_text("https://jobicy.com/api/v2/remote-jobs?count=50&geo=europe&industry=design-multimedia", timeout=20))
    except Exception:
        return []
    return [_row(j.get("jobTitle"), j.get("url"), j.get("jobGeo"), _text(j.get("jobDescription")), j.get("companyName"))
            for j in d.get("jobs") or []]


FEEDS = {"arbeitnow": _arbeitnow, "creativeboom": _creativeboom, "jobicy_eu": _jobicy}


def discover(cfg, classify, fetch_text, europe_location, boards=None, feeds=None):
    """Postings whose title belongs to the creative persona and sits in Europe or remote."""
    boards = CREATIVE_BOARDS if boards is None else boards
    feeds = FEEDS if feeds is None else feeds
    tasks = [(ats, s) for ats, slugs in boards.items() for s in slugs]
    found, SUMMARY_NEW = [], {}

    def keep(r):
        loc = r["location"]
        return (r["title"] and r["url"] and classify(r["title"]) == "creative"
                and (europe_location({"location": loc}) or (not loc) or _REMOTE_RE.search(loc)))

    with ThreadPoolExecutor(max_workers=16) as ex:
        for ats, slug, rows in ex.map(_board, tasks):
            if rows is None:
                continue
            hits = [r for r in rows if keep(r)]
            SUMMARY_NEW[f"{ats}:{slug}"] = len(hits)
            found += [{**r, "src": f"{ats}:{slug}"} for r in hits]
        for name, fn in feeds.items():
            try:
                rows = fn(fetch_text)
            except Exception:
                rows = []
            hits = [r for r in rows if keep(r)]
            SUMMARY_NEW[f"feed:{name}"] = len(hits)
            found += [{**r, "src": f"feed:{name}"} for r in hits]
    SUMMARY.clear()
    SUMMARY.update(SUMMARY_NEW)
    return found
