"""Official visa-sponsor registers and configurable vacancy eligibility heuristics.

  UK  Home Office "Register of licensed sponsors: workers" (daily CSV on gov.uk)
  NL  IND public register of recognised sponsors, "work" (labour + highly skilled)

Both are cached in autopilot/cache/ and refreshed weekly. `visa_path(job)` gives the
reason a posting is reachable, or None. `discover_postings` walks register companies
that look like tech/security/media firms and reads their public ATS boards: postings
there often never reach an aggregator."""
import csv, html, io, json, re, sys, threading, unicodedata, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "autopilot/cache"
BOARD_CACHE = CACHE / "sponsor_boards.json"
REFRESH = timedelta(days=7)
MISS_RETRY = timedelta(days=60)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
UK_API = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
NL_URL = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"
# routes a mid-level hire can come in on; seasonal / religious / charity / sport cannot
_UK_ROUTES = re.compile(r"skilled worker|scale-up|senior or specialist|expansion worker|creative worker", re.I)
NO_SPONSOR_RE = re.compile(
    r"(?:no|not|unable to|cannot|can't|do not|does not|don't|doesn't|will not|won't|are not able to|is not able to)"
    r" (?:\w+ ){0,3}(?:sponsor|provide (?:visa )?sponsorship|offer (?:visa )?sponsorship|support visa)"
    r"|without (?:the need for )?(?:visa )?sponsorship|must (?:already )?have (?:the )?(?:right|authori[sz]ation) to work", re.I)

# ---------- name normalization ----------
_SUFFIXES = set("limited ltd llp lp plc inc incorporated llc corp corporation co company gmbh mbh ag kg bv nv vof "
                "sa sas sarl srl spa ab as asa oy aps pty pvt holding holdings group uk gb nederland netherlands "
                "europe emea international intl".split())
_TRADING_AS = re.compile(r"\b(?:t/a|t/as|trading as)\b", re.I)


def norm(name):
    """'Monzo Bank Limited' -> 'monzo bank', 'Adyen N.V.' -> 'adyen'."""
    s = unicodedata.normalize("NFKD", html.unescape(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)", " ", re.sub(r"[.'`\"]", "", s)).replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", s)
    kept = [w for w in words if w not in _SUFFIXES]
    if kept[:1] == ["the"]:
        kept = kept[1:]
    return " ".join(kept or words)


def _keys(name):
    """Every name an organisation goes by: 'Foo Ltd T/A Bar' is both foo and bar."""
    return [k for k in (norm(p) for p in _TRADING_AS.split(name or "")) if k]


def _build(names):
    exact, prefixes = set(), set()
    for name in names:
        for k in _keys(name):
            exact.add(k)
            w = k.split()
            prefixes.update(" ".join(w[:i]) for i in range(1, len(w)))
    return exact, prefixes


def _match(company, index):
    exact, prefixes = index
    for k in _keys(company):
        if k in exact:
            return True
        # ponytail: word-prefix match both ways ("Monzo" ~ "Monzo Bank", "Revolut Business" ~
        # "Revolut"); a generic one-word name can hit an unrelated firm. Add a town check if it bites.
        if len(k) >= 4 and k in prefixes:
            return True
        w = k.split()
        if any(len(" ".join(w[:i])) >= 4 and " ".join(w[:i]) in exact for i in range(len(w) - 1, 0, -1)):
            return True
    return False


# ---------- registers (download, weekly cache, in-memory index) ----------
def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")


def _fetch_uk():
    m = re.search(r"https://assets\.publishing\.service\.gov\.uk/[^\"]+\.csv", _get(UK_API))
    if not m:
        raise RuntimeError("no CSV link on the gov.uk register page")
    rows, seen = [], set()
    for r in csv.DictReader(io.StringIO(_get(m.group(0), timeout=180))):
        name = (r.get("Organisation Name") or "").strip()
        if not name or "B rating" in (r.get("Type & Rating") or "") or not _UK_ROUTES.search(r.get("Route") or ""):
            continue
        if name not in seen:
            seen.add(name)
            rows.append([name, (r.get("Town/City") or "").strip()])
    return m.group(0), rows


def _fetch_nl():
    names = [html.unescape(re.sub(r"<[^>]+>", "", n)).replace('""', '"').strip(' "')
             for n in re.findall(r'<th scope="row">(.*?)</th>', _get(NL_URL), re.S)]
    return NL_URL, [[n, ""] for n in dict.fromkeys(names) if n]


_FETCH = {"uk": _fetch_uk, "nl": _fetch_nl}
_INDEX, _ROWS, _LOCK = {}, {}, threading.Lock()


def _rows(country):
    """[[name, town], ...] for a register, from the weekly cache or a fresh download."""
    if country in _ROWS:
        return _ROWS[country]
    path = CACHE / f"sponsors_{country}.json"
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not cached or datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched"]) > REFRESH:
        try:
            url, rows = _FETCH[country]()
            if rows:
                cached = {"fetched": datetime.now(timezone.utc).isoformat(), "source": url, "rows": rows}
                CACHE.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
        except Exception as exc:
            print(f"sponsors: {country} register download failed ({exc}); "
                  f"{'using the stale copy' if cached else 'no register, sponsor check falls back to the posting text'}",
                  file=sys.stderr, flush=True)
    return (cached or {}).get("rows") or []


def set_register(country, names):
    """Use `names` as the register (tests, or a manual override). No network."""
    with _LOCK:
        _ROWS[country] = [[n, ""] for n in names]
        _INDEX[country] = _build(names)


def _index(country):
    if country not in _INDEX:
        with _LOCK:
            if country not in _INDEX:
                rows = _rows(country)
                _ROWS[country] = rows
                _INDEX[country] = _build(r[0] for r in rows)
    return _INDEX[country]


_COUNTRY_ALIAS = {"gb": "uk", "united kingdom": "uk", "netherlands": "nl", "the netherlands": "nl"}


def is_known_sponsor(company, country):
    """True when `company` is on the official sponsor register of `country` (uk / nl)."""
    c = _COUNTRY_ALIAS.get((country or "").lower(), (country or "").lower())
    return c in _FETCH and bool(company) and _match(company, _index(c))


# ---------- where is the job ----------
def _rx(s):
    return re.compile(r"\b(?:" + s + r")\b", re.I)


# (always, unless the location is in the US/Canada): London ON, Cambridge MA, Atlanta, Georgia
_COUNTRIES = {
    "uk": (_rx(r"united kingdom|great britain|england|scotland|(?<!south )wales|northern ireland|uk|gb"),
           _rx(r"london|manchester|birmingham|edinburgh|glasgow|bristol|cambridge|oxford|leeds|liverpool|belfast|"
               r"cardiff|newcastle|nottingham|sheffield|brighton|milton keynes|aberdeen|dundee|leicester|coventry")),
    "nl": (_rx(r"netherlands|nederland|holland|amsterdam|rotterdam|utrecht|the hague|den haag|eindhoven|delft|"
               r"leiden|groningen|haarlem|amstelveen|hilversum|nijmegen|enschede"), None),
    "de": (_rx(r"germany|deutschland|berlin|munich|münchen|muenchen|frankfurt|hamburg|cologne|köln|stuttgart|"
               r"düsseldorf|dusseldorf|leipzig|dresden|karlsruhe|nuremberg|nürnberg|bonn|hannover|hanover"), None),
}
_NORTH_AMERICA = re.compile(r"\b(?:united states|usa|us|canada|ontario|massachusetts|alabama|atlanta)\b"
                            r"|,\s*(?:al|ma|ga|ny|ca|tx|wa|on|us)\b", re.I)


def job_countries(job):
    """Country codes named in the job's location field (the title only when there is none)."""
    text = str(job.get("location") or "") or str(job.get("title") or "")
    na = _NORTH_AMERICA.search(text)
    return [c for c, (always, cities) in _COUNTRIES.items()
            if always.search(text) or (cities and not na and cities.search(text))]


_EUROPE = _rx(
    r"europe|european|emea|eu|eea|schengen|germany|deutschland|france|spain|españa|portugal|italy|italia|netherlands|nederland|"
    r"belgi\w*|österreich|sverige|norge|danmark|suomi|polska|hilversum|noord-holland|zuid-holland|"
    r"belgium|luxembourg|austria|switzerland|schweiz|ireland|united kingdom|great britain|england|scotland|uk|"
    r"sweden|norway|denmark|finland|iceland|poland|czech\w*|slovakia|hungary|romania|bulgaria|greece|croatia|slovenia|"
    r"estonia|latvia|lithuania|malta|"
    r"berlin|munich|münchen|hamburg|cologne|köln|frankfurt|stuttgart|düsseldorf|leipzig|paris|lyon|marseille|"
    r"madrid|barcelona|valencia|lisbon|lisboa|porto|milan|milano|rome|roma|turin|amsterdam|rotterdam|utrecht|brussels|"
    r"antwerp|vienna|wien|zurich|zürich|geneva|dublin|london|manchester|edinburgh|stockholm|gothenburg|oslo|"
    r"copenhagen|helsinki|warsaw|krakow|kraów|prague|budapest|bucharest|sofia|athens|zagreb|ljubljana|tallinn|riga|vilnius")


def europe_location(job):
    """True when the posting sits in Europe (EU/EEA/UK/Switzerland, 'Europe' remote). North
    American namesakes (London ON, Paris TX, Berlin NH) don't count."""
    text = str(job.get("location") or "") or str(job.get("title") or "")
    return bool(_EUROPE.search(text)) and not _NORTH_AMERICA.search(text)


def sponsorship_waived(job, persona=None, cfg=None):
    """Optional user preference to waive the sponsorship mention for a persona.
    Configured in apply.sponsorship_waived = {persona: "europe"}."""
    persona = persona or job.get("persona")
    waived = ((cfg or {}).get("apply") or {}).get("sponsorship_waived") or {}
    return waived.get(persona) == "europe" and europe_location(job)


def licence_country(job, visa_free_location=None):
    """'uk' / 'nl' when the job sits only in a country whose employer needs a sponsor licence."""
    cs = job_countries(job)
    if visa_free_location and visa_free_location(job):
        return None
    return cs[0] if cs and set(cs) <= {"uk", "nl"} else None


def visa_path(job, persona=None, offers_sponsorship=None, needs_no_visa=None, visa_free_location=None, cfg=None):
    """Why the candidate can realistically get to work on this job, or None.
    `offers_sponsorship` / `needs_no_visa` / `visa_free_location` are autopilot's checks
    (posting text; worldwide remote; UAE/Serbia/Armenia/Georgia/... location)."""
    persona = persona or job.get("persona")
    # "Remote - US" is not worldwide, whatever the title says ("Global Security ...")
    if needs_no_visa and not _NORTH_AMERICA.search(str(job.get("location") or "")) and needs_no_visa(job):
        return "remote worldwide: no work permit involved"
    place = visa_free_location(job) if visa_free_location else ""
    if place:
        return f"{place}: no employer sponsorship needed for Russian citizens"
    # A licence or the Europe waiver cannot overrule this vacancy's explicit restriction.
    description = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(job.get("description") or "")).split())
    if NO_SPONSOR_RE.search(description):
        return None
    if sponsorship_waived(job, persona, cfg):
        return "Europe creative role: sponsorship requirement waived by the user"
    company = job.get("company") or ""
    for c in job_countries(job):
        if c == "uk" and is_known_sponsor(company, "uk"):
            return "UK: company is on the Home Office register of licensed sponsors"
        if c == "nl" and is_known_sponsor(company, "nl"):
            return "NL: company is an IND recognised sponsor"
        if c == "de" and persona == "appsec":
            return "Germany: EU Blue Card, no employer licence needed"
    if offers_sponsorship and offers_sponsorship(job):
        return "posting offers visa sponsorship / relocation"
    return None


# ---------- hidden vacancies: register companies' own ATS boards ----------
_STRONG = _rx(r"cyber|infosec|cybersecurity|software|media|studios?|films?|pictures|photo\w*|video\w*|"
              r"animation|vfx|games|gaming")
_WEAK = _rx(r"security|tech\w*|digital|data|cloud|labs?|ai|analytics|computing|networks?|fintech|platforms?|"
            r"apps?|saas|robotics|quantum|crypto|blockchain|web|online|internet|productions?|creative|content|"
            r"broadcast\w*|entertainment|interactive|it")
_NEG = _rx(r"care|nursing|recruit\w*|staffing|restaurants?|cafe|takeaway|kitchen|construction|cleaning|catering|"
           r"dental|pharmacy|guard\w*|security services|logistics|transport|motors?|food|beauty|salon|barbers?|"
           r"church|builders?|clinic|hotels?|travel|propert(?:y|ies)|farm\w*|school|college|university|academy")


def name_score(name):
    """2 = clearly tech/security/media, 1 = maybe, 0 = skip. A name-only heuristic."""
    if _NEG.search(name or ""):
        return 0
    return 2 if _STRONG.search(name) else 1 if _WEAK.search(name) else 0


def _read_board_cache():
    try:
        return json.loads(BOARD_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_WK_SEM = threading.Semaphore(8)   # Workable answers 429 beyond a few parallel requests


def _workable(slug, timeout):
    import time, urllib.error
    import ats_schema as A
    for attempt in range(3):
        with _WK_SEM:
            try:
                return A._workable_board(slug, timeout)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise
        time.sleep(4 * (attempt + 1))


def _probe(key, cached, timeout):
    """(board, postings, sure): board is [ats, slug] or None; sure=False when a request
    failed for a reason other than a 404, so the miss is retried after a week, not 60 days."""
    import urllib.error
    import ats_schema as A
    fetchers = {"greenhouse": A._gh_board, "ashby": A._ashby_board, "lever": A._lever_board, "workable": _workable}
    if cached:
        try:
            return cached, fetchers[cached[0]](cached[1], timeout), True
        except Exception:
            return cached, [], True
    words, sure = key.split(), True
    # no first-word-only slug: "apex" for "Apex Media" lands on someone else's board.
    # Workable is left out of blind probing: it answers 429 under parallel load.
    for slug in dict.fromkeys(["".join(words), "-".join(words)]):
        if len(slug) < 3:
            continue
        for ats in ("greenhouse", "ashby", "lever"):
            try:
                jobs = fetchers[ats](slug, timeout)
            except urllib.error.HTTPError as exc:
                sure = sure and exc.code in (404, 410)
                continue
            except Exception:
                sure = False
                continue
            if jobs:
                return [ats, slug], jobs, True
    return None, [], sure


_BOARD_URL = [("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([\w-]+)/jobs/")),
              ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/")),
              ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)/")),
              ("workable", re.compile(r"apply\.workable\.com/([\w-]+)/j/"))]


def _seen_boards():
    """({company: [ats, slug]} for every ATS board a stored job points at, {stored urls})."""
    import sqlite3
    out, urls = {}, set()
    try:
        con = sqlite3.connect(f"file:{(ROOT / 'autopilot/jobs.db').as_posix()}?mode=ro", uri=True, timeout=30)
        for company, url in con.execute(
                "SELECT DISTINCT company, url FROM jobs WHERE url LIKE '%greenhouse.io/%' OR url LIKE "
                "'%ashbyhq.com/%' OR url LIKE '%lever.co/%' OR url LIKE '%workable.com/%'"):
            for ats, rx in _BOARD_URL:
                m = rx.search(url or "")
                if m and company:
                    out.setdefault(company, [ats, m.group(1)])
                    break
        urls = {u for (u,) in con.execute("SELECT url FROM jobs") if u}
        con.close()
    except Exception as exc:
        print(f"sponsors: could not read known boards from jobs.db ({exc})", file=sys.stderr, flush=True)
    return out, urls


def discover_postings(cfg, limit, classify, offers_sponsorship=None, needs_no_visa=None,
                      visa_free_location=None, per_run=None, workers=48, timeout=20):
    """Postings (title/company/url/location/description dicts) from the public ATS boards of
    registered UK/NL sponsors, kept only when a persona fits the title and the job has a
    visa path. Boards come from (a) companies already in jobs.db whose name is on a register
    (their other postings rarely reach aggregators) and (b) blind probing of register names
    that look like tech/security/media firms, cached in autopilot/cache/sponsor_boards.json."""
    from concurrent.futures import ThreadPoolExecutor
    s_cfg = cfg.get("sponsors") or {}
    # ponytail: ~6 s per request through the local proxy, so a run probes 100 new
    # companies (~1-2 min); raise sponsors.companies_per_run in config for longer runs
    per_run = int(per_run or s_cfg.get("companies_per_run", 100))
    cache = _read_board_cache()
    today = datetime.now(timezone.utc).date()
    boards, stored = _seen_boards()
    for company, board in boards.items():
        country = next((c for c in ("uk", "nl") if is_known_sponsor(company, c)), None)
        if country and not (cache.get(norm(company)) or {}).get("board"):
            cache[norm(company)] = {"t": today.isoformat(), "board": board, "name": company, "country": country}
    known = [(k, e["name"], e["country"], e["board"]) for k, e in cache.items()
             if e.get("board") and e.get("name") and e.get("country")]
    fresh, seen = [], set()
    cands = sorted(((name_score(n), n, c) for c in ("uk", "nl") for n, _town in _rows(c)),
                   key=lambda x: (-x[0], x[1].lower()))
    for score, name, country in cands:
        if not score:
            break
        key = norm(name)
        if key in seen:
            continue
        seen.add(key)
        e = cache.get(key)
        if not e or (not e.get("board") and today - date.fromisoformat(e["t"])
                     > (MISS_RETRY if e.get("sure", True) else REFRESH)):
            fresh.append((key, name, country, None))
    todo = fresh[:per_run] + known[:int(s_cfg.get("known_boards_per_run", 400))]

    def run(x):
        try:
            return x, _probe(x[0], x[3], timeout)
        except Exception:
            return x, (x[3], [], False)
    new_jobs, old_jobs, boards_found = [], [], 0
    label = {"uk": "UK Home Office register of licensed sponsors", "nl": "IND register of recognised sponsors"}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for (key, name, country, was), (board, postings, sure) in ex.map(run, todo):
            cache[key] = {"t": today.isoformat(), "board": board, "name": name, "country": country,
                          **({} if board or sure else {"sure": False})}
            if board and not was:
                boards_found += 1
            for title, url, loc in postings:
                persona = classify(title, cfg)
                if not persona or not url:
                    continue
                job = {"title": title, "company": name, "url": url, "location": loc, "persona": persona,
                       "description": f"{name} is on the {label[country]} (board {board[0]}/{board[1]})."}
                if visa_path(job, persona, offers_sponsorship, needs_no_visa, visa_free_location):
                    (old_jobs if was else new_jobs).append(job)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = BOARD_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(BOARD_CACHE)
    except OSError as exc:
        print(f"sponsors: board cache not saved ({exc})", file=sys.stderr, flush=True)
    # postings not stored yet first, so --limit is spent on them
    jobs = sorted(new_jobs + old_jobs, key=lambda j: j["url"] in stored)
    print(f"sponsors: {len(known)} known sponsor boards re-read, {len(fresh[:per_run])} of {len(fresh)} unprobed "
          f"tech/security/media register names probed ({boards_found} new boards) -> {len(jobs)} postings "
          f"that fit a persona and have a visa path ({sum(j['url'] not in stored for j in jobs)} not stored yet)", file=sys.stderr, flush=True)
    return jobs[:limit] if limit else jobs


if __name__ == "__main__":
    for c in ("uk", "nl"):
        exact, _ = _index(c)
        print(f"{c}: {len(_ROWS[c])} register rows, {len(exact)} normalized names")
