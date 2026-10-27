"""Read an application form's questions from the ATS's own public API instead of
guessing them from the rendered DOM. Verified endpoints (no key needed):
  Greenhouse  GET  boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true
              (job-boards.eu.greenhouse.io boards are served by the same API)
  Ashby       POST jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting
  Workable    GET  apply.workable.com/api/v1/jobs/{shortcode}/form
Lever is not covered (automatic runs exclude it). Every question comes back as
{label, required, type, options, name} with type one of text / textarea / select /
multiselect / yesno / file / date / number / checkbox."""
import functools, html, json, re, urllib.request
from urllib.parse import urlparse, parse_qs, unquote

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
_YESNO = ["Yes", "No"]


def _get_json(url, timeout, body=None):
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _clean(label):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", label or ""))).strip()


def _q(label, required, kind, options, name):
    options = [_clean(o) for o in options if o]
    if kind in ("select", "checkbox") and sorted(o.lower() for o in options) == ["no", "yes"]:
        kind = "yesno"
    return {"label": _clean(label), "required": bool(required), "type": kind, "options": options, "name": name}


_GH_TYPES = {"input_text": "text", "textarea": "textarea", "input_file": "file",
             "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}


def normalize_greenhouse(data):
    compliance = [q for block in data.get("compliance") or [] for q in block.get("questions") or []]
    out = []
    for q in (data.get("questions") or []) + (data.get("location_questions") or []) + compliance:
        fields = [f for f in q.get("fields") or [] if f.get("type") != "input_hidden"]
        if not fields:
            continue
        f = fields[0]
        out.append(_q(q.get("label"), q.get("required"), _GH_TYPES.get(f.get("type"), f.get("type") or "text"),
                      [v.get("label") for v in f.get("values") or []], f.get("name")))
    return out


_ASHBY_TYPES = {"String": "text", "Email": "text", "Phone": "text", "Location": "text", "LongText": "textarea",
                "File": "file", "ValueSelect": "select", "MultiValueSelect": "multiselect", "Boolean": "yesno",
                "Date": "date", "Number": "number"}

_ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id
    applicationForm { sections { fieldEntries { ... on FormFieldEntry { isRequired field } } } }
  }
}"""


def normalize_ashby(data):
    posting = (data.get("data") or {}).get("jobPosting")
    if not posting:
        return None
    out = []
    for section in (posting.get("applicationForm") or {}).get("sections") or []:
        for e in section.get("fieldEntries") or []:
            f = e.get("field") or {}
            if not f.get("title"):
                continue
            kind = _ASHBY_TYPES.get(f.get("type"), (f.get("type") or "text").lower())
            opts = _YESNO if kind == "yesno" else [v.get("label") for v in f.get("selectableValues") or []]
            out.append(_q(f["title"], e.get("isRequired"), kind, opts, f.get("path")))
    return out


_WK_TYPES = {"text": "text", "email": "text", "phone": "text", "paragraph": "textarea", "file": "file",
             "dropdown": "select", "boolean": "yesno", "date": "date", "number": "number"}


def normalize_workable(data):
    out = []
    for section in data if isinstance(data, list) else []:
        for f in section.get("fields") or []:
            if f.get("type") == "group":
                continue   # ponytail: repeatable education/experience blocks, filled from the CV
            kind = _WK_TYPES.get(f.get("type"), f.get("type") or "text")
            if f.get("type") == "multiple":
                kind = "select" if f.get("singleOption") else "multiselect"
            opts = _YESNO if kind == "yesno" else [o.get("value") for o in f.get("options") or []]
            out.append(_q(f.get("label") or f.get("id"), f.get("required"), kind, opts, f.get("id")))
    return out


def _route(url):
    """(kind, a, b) for a supported posting URL, else None."""
    u = urlparse(url or "")
    host, parts, qs = u.netloc.lower(), [unquote(p) for p in u.path.split("/") if p], parse_qs(u.query)
    if host.endswith("greenhouse.io"):
        if qs.get("for") and qs.get("token"):          # /embed/job_app?for=elastic&token=123
            return "greenhouse", qs["for"][0], qs["token"][0]
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return "greenhouse", parts[0], parts[2]
        return None   # a custom domain with only ?gh_jid= has no board to ask
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return "ashby", parts[0], parts[1]
    if host == "apply.workable.com" and "j" in parts and parts.index("j") + 1 < len(parts):
        return "workable", None, parts[parts.index("j") + 1]
    return None


@functools.lru_cache(maxsize=512)
def fetch_schema(url, timeout=15):
    """Normalized questions for this posting, or None (unsupported host, dead listing,
    network error). Cached per URL for the life of the process."""
    try:
        route = _route(url)
        if not route:
            return None
        kind, a, b = route
        if kind == "greenhouse":
            return normalize_greenhouse(_get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{a}/jobs/{b}?questions=true", timeout))
        if kind == "ashby":
            return normalize_ashby(_get_json(
                "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", timeout,
                {"operationName": "ApiJobPosting", "query": _ASHBY_QUERY,
                 "variables": {"organizationHostedJobsPageName": a, "jobPostingId": b}}))
        return normalize_workable(_get_json(f"https://apply.workable.com/api/v1/jobs/{b}/form", timeout))
    except Exception:
        return None


# "Do you hold an active clearance?" is a gate; "are you willing/able to obtain one" is not
_CLEARANCE_Q = re.compile(r"\bclearance\b|\bpolygraph\b")
_HAVE_Q = re.compile(r"\b(?:have|hold|holds|possess|active|current|currently|held)\b")
_WILLING_Q = re.compile(r"\b(?:willing|able to|eligible|obtain|apply for|undergo|get|acquire|sponsor)\b")
_CITIZEN_Q = re.compile(r"\b(?:u\.?s\.?|united states|american) citizen")
_MUST_Q = re.compile(r"\b(?:must|required|requires|require|only)\b|export control|\bitar\b")


def knockout(schema, answer_fn, phrases=()):
    """The first REQUIRED question this profile can't truthfully pass, as a short reason,
    else None. Conservative: only a yes/no clearance gate, a yes/no "must be a US
    citizen" gate, or a required label carrying one of `phrases` (autopilot's
    _HARD_DISQUALIFY_PHRASES) - and never when answer_fn(label, options) says "Yes"."""
    for q in schema or []:
        if not q.get("required"):
            continue
        text = q["label"].lower()
        reason = next((p for p in phrases if p in text), None)
        if not reason and q.get("type") == "yesno":
            if _CLEARANCE_Q.search(text) and _HAVE_Q.search(text) and not _WILLING_Q.search(text):
                reason = "requires an active security clearance"
            elif _CITIZEN_Q.search(text) and _MUST_Q.search(text):
                reason = "requires US citizenship"
        if reason and (answer_fn(q["label"], q.get("options") or []) or "").strip().lower() != "yes":
            return f"{reason}: {q['label'][:90]}"
    return None


# ---- find a posting on the company's own board (for LinkedIn / aggregator finds) ----
# A logged-out LinkedIn page marks an offsite apply but no longer exposes its URL, so
# jobspy's job_url_direct comes back empty. The company's public board lists the same
# posting under the same title — that board URL is what the apply pipeline can fill.

_CORP_SUFFIX = re.compile(r"\b(inc|ltd|llc|plc|gmbh|ag|se|sa|bv|nv|ab|as|oy|group|holding|holdings|"
                          r"technologies|technology|labs|europe|latam|emea|uk|de)\b\.?")
_TITLE_NOISE = re.compile(r"\((?:m|f|w|d|x|all genders?|remote|hybrid)(?:[/|,]\s*\w+)*\)|\bm/[fw]/d\b|\bf/m/d\b", re.I)
_TITLE_STOP = {"the", "a", "an", "and", "of", "for", "in", "at", "to", "with", "-", "remote", "hybrid"}


def _board_slugs(company):
    base = _CORP_SUFFIX.sub(" ", (company or "").lower().replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", base)
    out = []
    for s in ("".join(words), "-".join(words), words[0] if words else ""):
        if len(s) >= 2 and s not in out:
            out.append(s)
    return out


@functools.lru_cache(maxsize=4096)
def company_board(company, timeout=10, slugs=()):
    """((title, url, location), ...) from the company's Greenhouse, Ashby or Workable
    board, or (). `slugs` are tried first (a known board id, e.g. a YC slug)."""
    from concurrent.futures import ThreadPoolExecutor
    def fetch_one(args):
        try:
            return args[0](args[1], timeout)
        except Exception:
            return []
    # the four ATS are asked at once (mostly 404s, ~4 s each through a proxy); the first
    # board in the usual order that has postings still wins
    with ThreadPoolExecutor(max_workers=4) as ex:
        for slug in dict.fromkeys([*slugs, *_board_slugs(company)]):
            if not slug:
                continue
            for jobs in ex.map(fetch_one, [(f, slug) for f in (_gh_board, _ashby_board, _workable_board, _lever_board)]):
                if jobs:
                    return tuple(jobs)
    return ()


def _gh_board(slug, timeout):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout)
    # canonical board URL, not absolute_url: a custom-domain "?gh_jid=" link hides the
    # board, and fetch_schema needs it
    return [(j.get("title") or "", f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}",
             (j.get("location") or {}).get("name") or "")
            for j in data.get("jobs") or [] if j.get("id")]


def _ashby_board(slug, timeout):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout)
    return [(j.get("title") or "", j["jobUrl"], j.get("location") or "")
            for j in data.get("jobs") or [] if j.get("jobUrl")]


def _workable_board(slug, timeout):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout)
    return [(j.get("title") or "", j.get("url") or f"https://apply.workable.com/j/{j['shortcode']}",
             ", ".join(x for x in (j.get("city"), j.get("country")) if x))
            for j in data.get("jobs") or [] if j.get("url") or j.get("shortcode")]


def _lever_board(slug, timeout):
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout)
    if not isinstance(data, list):
        return []
    return [(j.get("text") or "", j.get("hostedUrl") or "", (j.get("categories") or {}).get("location") or "")
            for j in data if j.get("hostedUrl")]


def _title_words(title):
    t = _TITLE_NOISE.sub(" ", (title or "").lower())
    return {w for w in re.findall(r"[a-z0-9+#]+", t) if w not in _TITLE_STOP}


def find_on_company_board(company, title, board=None):
    """URL of the posting titled `title` on `company`'s board, or None. Needs a near
    exact title: "Senior Security Engineer" must not land on "Security Engineer"."""
    board = company_board(company) if board is None else board
    want = _title_words(title)
    if not want:
        return None
    best, best_score = None, 0.0
    for t, url, *_ in board:
        have = _title_words(t)
        if not have:
            continue
        if have == want:
            return url
        score = len(want & have) / len(want | have)
        if score > best_score:
            best, best_score = url, score
    return best if best_score >= 0.85 else None
