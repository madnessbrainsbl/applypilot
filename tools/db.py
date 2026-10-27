"""SQLite store for scraped vacancies + application attempts.

Replaces the old jobs.jsonl/applied.jsonl append-only files (see the removed
"swap for sqlite when concurrent runs matter" note in autopilot.py). Jobs are
UPSERTed by id, so re-running `discover` updates score/description on already
-seen listings instead of only ever appending new ones.
"""
import json, os, re, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "autopilot/jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, keyword TEXT, title TEXT, company TEXT, location TEXT,
    url TEXT, description TEXT, score INTEGER, cv TEXT, status TEXT,
    created_at TEXT, updated_at TEXT, dead_listing INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0, claim_token TEXT, claimed_at TEXT,
    salary TEXT, persona TEXT DEFAULT 'appsec'
);
CREATE TABLE IF NOT EXISTS applications (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id),
    applied_at TEXT, dry_run INTEGER, review INTEGER, submitted INTEGER,
    dead_listing INTEGER DEFAULT 0, filled_fields INTEGER,
    cv_used TEXT, cover_used TEXT, screenshot TEXT,
    needs_manual INTEGER DEFAULT 0, apply_url TEXT, manual_reason TEXT,
    persona TEXT DEFAULT 'appsec', confirmation TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_apps_job ON applications(job_id);
CREATE TABLE IF NOT EXISTS qa_answers (
    question_key TEXT PRIMARY KEY,
    question_label TEXT,
    answer TEXT,
    company TEXT,
    hits INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_questions (
    question_key TEXT PRIMARY KEY,
    question_label TEXT,
    options TEXT,
    company TEXT,
    job_url TEXT,
    seen INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS answer_decisions (
    question_key TEXT NOT NULL, job_url TEXT NOT NULL, persona TEXT,
    question TEXT, company TEXT, source TEXT, method TEXT, status TEXT,
    draft TEXT, answer TEXT, reason TEXT, evidence TEXT, context_key TEXT,
    cv TEXT, checked_at TEXT,
    PRIMARY KEY (question_key, job_url)
);
CREATE INDEX IF NOT EXISTS idx_answer_decisions_context ON answer_decisions(context_key);
CREATE TABLE IF NOT EXISTS run_locks (
    name TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id),
    status TEXT,
    noted_at TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_job ON outcomes(job_id);
CREATE TABLE IF NOT EXISTS outcome_details (
    job_id TEXT REFERENCES jobs(id),
    status TEXT,
    kind TEXT,
    reason TEXT,
    hours REAL,
    email_date TEXT,
    subject TEXT,
    sender TEXT,
    link TEXT,
    done INTEGER DEFAULT 0,
    PRIMARY KEY (job_id, status)
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id),
    company TEXT,
    name TEXT,
    role TEXT,
    email TEXT,
    email_source TEXT,
    confidence REAL DEFAULT 0.8,
    linkedin_url TEXT,
    pitch_subject TEXT,
    pitch_body TEXT,
    status TEXT DEFAULT 'discovered',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company);
CREATE INDEX IF NOT EXISTS idx_leads_job ON leads(job_id);
CREATE TABLE IF NOT EXISTS auto_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,
    in_reply_to TEXT,
    recipient TEXT,
    subject TEXT,
    intent TEXT,
    reply_body TEXT,
    sent_at TEXT,
    status TEXT DEFAULT 'sent'
);
CREATE INDEX IF NOT EXISTS idx_replies_msg ON auto_replies(message_id);
CREATE TABLE IF NOT EXISTS source_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, persona TEXT, started_at TEXT, finished_at TEXT,
    found INTEGER DEFAULT 0, stored INTEGER DEFAULT 0,
    queued_after INTEGER DEFAULT 0, errors INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_source_runs_src ON source_runs(source, finished_at);
"""

OUTCOME_STATUSES = ["replied", "screening", "interview", "offer",
                    "hired", "rejected", "no_response", "withdrawn"]

FINAL_STATUSES = {"hired", "rejected", "no_response", "withdrawn"}

POSITIVE_STATUSES = {"replied", "screening", "interview", "offer", "hired"}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    _migrate_columns(con)
    _migrate_jsonl(con)
    return con


def _migrate_columns(con):
    """CREATE TABLE IF NOT EXISTS doesn't add columns to an already-existing
    table \u2014 patch older jobs.db files created before these columns existed."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
    if "hidden" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN hidden INTEGER DEFAULT 0")
    if "claim_token" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN claim_token TEXT")
    if "claimed_at" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN claimed_at TEXT")
    if "salary" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN salary TEXT")
    if "persona" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN persona TEXT DEFAULT 'appsec'")
    # CV<->description keyword match, cached; cv_match_key names the CV file+mtime and
    # the text it was computed from, so a new CV or an edited posting recomputes
    if "cv_match" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN cv_match REAL")
    if "cv_match_key" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN cv_match_key TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_persona ON jobs(persona)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    acols = {r["name"] for r in con.execute("PRAGMA table_info(applications)").fetchall()}
    for col, ddl in (("needs_manual", "needs_manual INTEGER DEFAULT 0"),
                     ("apply_url", "apply_url TEXT"), ("manual_reason", "manual_reason TEXT"),
                     ("persona", "persona TEXT DEFAULT 'appsec'"), ("confirmation", "confirmation TEXT")):
        if col not in acols:
            con.execute(f"ALTER TABLE applications ADD COLUMN {ddl}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_apps_submitted ON applications(submitted, applied_at)")
    con.commit()


def _migrate_jsonl(con):
    """One-time import of the legacy .jsonl files so existing queue/history
    isn't lost on the first run after switching to sqlite."""
    root = DB_PATH.parent
    if con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0:
        p = root / "jobs.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    upsert_job(con, json.loads(line), commit=False)
    if con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0:
        p = root / "applied.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    insert_application(con, json.loads(line), commit=False)
    if con.execute("SELECT COUNT(*) FROM qa_answers").fetchone()[0] == 0:
        p = root / "qa_memory.json"
        if p.exists():
            now = datetime.now(timezone.utc).isoformat()
            for key, answer in json.loads(p.read_text(encoding="utf-8")).items():
                con.execute(
                    "INSERT OR IGNORE INTO qa_answers (question_key, question_label, answer, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (key, key, answer, now, now),
                )
    con.commit()


def job_exists(con, job_id):
    return con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is not None


def upsert_job(con, job, commit=True):
    data = dict(job)
    data.setdefault("salary", None)
    data.setdefault("persona", "appsec")
    con.execute(
        """INSERT INTO jobs (id, source, keyword, title, company, location, url,
               description, score, cv, status, salary, persona, created_at, updated_at)
           VALUES (:id, :source, :keyword, :title, :company, :location, :url,
               :description, :score, :cv, :status, :salary, :persona, :created_at, :created_at)
           ON CONFLICT(id) DO UPDATE SET
               score=excluded.score, description=excluded.description,
               title=excluded.title, company=excluded.company, location=excluded.location,
               status=CASE WHEN jobs.status IN ('applied','manual','manual_contact','dead_listing','pending','pending_confirmation')
                           THEN jobs.status ELSE excluded.status END, cv=excluded.cv,
               salary=COALESCE(excluded.salary, jobs.salary),
               persona=COALESCE(excluded.persona, jobs.persona, 'appsec'),
               updated_at=excluded.created_at""",
        data,
    )
    if commit: con.commit()


_APPLICATION_DEFAULTS = {"dry_run": 0, "review": 0, "submitted": 0, "dead_listing": 0,
                          "filled_fields": 0, "cv_used": None, "cover_used": None, "screenshot": None,
                          "needs_manual": 0, "apply_url": None, "manual_reason": None,
                          "persona": "appsec", "confirmation": None}


def insert_application(con, rec, commit=True):
    data = {**_APPLICATION_DEFAULTS, **rec}
    if isinstance(data["confirmation"], dict):
        data["confirmation"] = json.dumps(data["confirmation"], ensure_ascii=False)
    if not rec.get("persona") and rec.get("id"):
        j_p = con.execute("SELECT persona FROM jobs WHERE id=?", (rec["id"],)).fetchone()
        if j_p and j_p[0]:
            data["persona"] = j_p[0]
    con.execute(
        """INSERT INTO applications (job_id, applied_at, dry_run, review, submitted,
               dead_listing, filled_fields, cv_used, cover_used, screenshot,
               needs_manual, apply_url, manual_reason, persona, confirmation)
           VALUES (:id, :applied_at, :dry_run, :review, :submitted, :dead_listing,
               :filled_fields, :cv_used, :cover_used, :screenshot,
               :needs_manual, :apply_url, :manual_reason, :persona, :confirmation)""",
        data,
    )
    if rec.get("dead_listing"):
        con.execute("UPDATE jobs SET dead_listing=1 WHERE id=?", (rec["id"],))
    if rec.get("submitted"):
        con.execute("UPDATE jobs SET status='applied', updated_at=datetime('now') WHERE id=?", (rec["id"],))
    if commit: con.commit()


def manual_needed(con):
    """Jobs the bot filled but couldn't auto-submit (Ashby spam-block, captcha, etc.)
    and that haven't since been resolved \u2014 the dashboard shows these as alerts with a
    link so the user can finish them by hand. One row per job (latest attempt); drops
    any job later marked submitted or dismissed."""
    rows = con.execute(
        """SELECT a.job_id, a.apply_url, a.manual_reason, a.applied_at, a.screenshot, a.filled_fields,
                  j.company, j.title
           FROM applications a
           JOIN jobs j ON j.id = a.job_id
           WHERE a.needs_manual = 1
             AND a.attempt_id = (SELECT MAX(attempt_id) FROM applications b WHERE b.job_id = a.job_id AND b.needs_manual = 1)
             AND NOT EXISTS (SELECT 1 FROM applications s WHERE s.job_id = a.job_id AND s.submitted = 1)
           ORDER BY a.applied_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def dismiss_manual(con, job_id):
    """User handled it (or wants it gone): clear the needs_manual flag for this job."""
    con.execute("UPDATE applications SET needs_manual=0 WHERE job_id=?", (job_id,))
    con.commit()


# A watchdog stall before the form is touched cannot have sent anything: the first one
# leaves the vacancy eligible, a second one skips it. A stall at any later step stays
# skipped at once (the outcome is unknown; a timeout never authorizes another send).
_SAFE_STALL_STEPS = ("start", "ats schema", "launch browser", "open page", "resolve apply url", "cover letter")
_SAFE_STALL = " OR ".join(f"manual_reason LIKE '%stalled the run at step ''{x}''%'" for x in _SAFE_STALL_STEPS)


def skip_ids(con):
    """Exclude submitted, manual and delisted jobs. Unconfirmed attempts remain
    for human review regardless of age; a timeout never authorizes another send."""
    rows = con.execute(
        f"""SELECT job_id FROM applications WHERE submitted=1
           UNION SELECT id FROM jobs WHERE dead_listing=1
           UNION SELECT job_id FROM applications WHERE needs_manual=1 AND NOT coalesce({_SAFE_STALL}, 0)
           UNION SELECT id FROM jobs WHERE status IN ('pending','pending_confirmation')
           UNION SELECT job_id FROM applications WHERE coalesce(dry_run,0)=0 AND manual_reason LIKE '%confirm%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%blocked this automated session as spam%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%signing in with a Google account%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%creating or signing in to a site account%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%posting requires pre-existing eligibility%'
           UNION SELECT job_id FROM (SELECT job_id FROM applications WHERE {_SAFE_STALL}
                                     GROUP BY job_id HAVING COUNT(*) >= 2)"""
    ).fetchall()
    return {r[0] for r in rows}


_JOB_COLS_NO_DESC = ("id, source, keyword, title, company, location, url, score, cv, status, created_at, updated_at, "
                     "dead_listing, hidden, claim_token, claimed_at, salary, persona, cv_match, cv_match_key")

STALENESS_PENALTY = """
    CASE
        WHEN julianday('now') - julianday(created_at) < 3  THEN 0
        WHEN julianday('now') - julianday(created_at) < 7  THEN 5
        WHEN julianday('now') - julianday(created_at) < 14 THEN 15
        WHEN julianday('now') - julianday(created_at) < 30 THEN 30
        ELSE 50
    END
"""

TIER_BOOST = """
    CASE
        WHEN LOWER(company) IN ('canonical', 'jetbrains', 'nebius', 'gitlab', 'supabase', 'brave', 'sumup', 'proton', 'automattic', 'semgrep', 'flo health', 'monzo') THEN 15
        WHEN LOWER(location) LIKE '%worldwide%' OR LOWER(location) LIKE '%global%' OR LOWER(location) LIKE '%anywhere%' OR LOWER(location) LIKE '%remote - europe%' OR LOWER(location) LIKE '%emea%' THEN 10
        ELSE 0
    END
"""


def eligible_jobs(con, min_score, persona=None, with_description=True):
    # ponytail: callers that only count (status) pass with_description=False; the apply gate reads the text, so it keeps it
    skip = skip_ids(con)
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    claimed_rows = con.execute("SELECT id, claim_token FROM jobs WHERE claim_token IS NOT NULL").fetchall()
    dead_ids = []
    for r in claimed_rows:
        jid = r["id"] if isinstance(r, (dict, sqlite3.Row)) else r[0]
        tok = r["claim_token"] if isinstance(r, (dict, sqlite3.Row)) else r[1]
        pid = _token_pid(tok)
        if pid > 0 and not _pid_alive(pid):
            dead_ids.append(jid)
    if dead_ids:
        placeholders = ",".join("?" for _ in dead_ids)
        con.execute(f"UPDATE jobs SET claim_token=NULL, claimed_at=NULL WHERE id IN ({placeholders})", dead_ids)
        con.commit()

    where_extra = ""
    params = [min_score, stale]
    if persona and persona != "all":
        where_extra = "AND (persona = ? OR (persona IS NULL AND ? = 'appsec'))"
        params.extend([persona, persona])

    cols = "*" if with_description else _JOB_COLS_NO_DESC
    rows = con.execute(
        f"""SELECT {cols}, score - ({STALENESS_PENALTY}) + ({TIER_BOOST}) AS priority FROM jobs
           WHERE score >= ? AND dead_listing=0 AND hidden=0
           AND (claim_token IS NULL OR claimed_at < ?)
           {where_extra}
           ORDER BY priority DESC, score DESC""", tuple(params)
    ).fetchall()
    return [dict(r) for r in rows if r["id"] not in skip]


def _slug(s):
    # same as autopilot.slug \u2014 importing autopilot here would be circular
    return re.sub(r"[^a-z0-9]+", "-", (s or "job").lower()).strip("-")[:80] or "job"


def _recent_submitted(con, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return con.execute(
        """SELECT DISTINCT a.job_id, j.company, j.title FROM applications a
           JOIN jobs j ON j.id = a.job_id
           WHERE (a.submitted = 1
                  OR (a.needs_manual = 1 AND coalesce(a.dry_run,0) = 0 AND a.manual_reason LIKE '%confirm%'))
             AND a.applied_at >= ?""", (since,)).fetchall()


def recent_company_counts(con, days):
    """{company_slug: submitted applications in the last `days` days} \u2014 the
    per-run company cap has no memory, so repeated runs kept hitting the same
    company (Ashby: ~5 per 180 days, spam-blocks bursts). applied_at is Python
    isoformat, so the cut-off is too: SQLite datetime('now') uses a different
    format and string comparison against it is wrong."""
    out = {}
    for _jid, company, _t in _recent_submitted(con, days):
        out[_slug(company)] = out.get(_slug(company), 0) + 1
    return out


_LOC = (r"remote|hybrid|emea|apac|amer|latam|europe|eu|uk|us|usa|germany|berlin|london|munich|amsterdam|paris|dublin|"
        r"warsaw|madrid|barcelona|lisbon|zurich|vienna|singapore|new york|san francisco|dubai|belgrade|toronto|sydney")
_GENDER_RE = re.compile(r"\b[mwfdx]\s*[/|,]\s*[mwfdx*]\b(?:\s*[/|,]\s*[mwfdx*]\b)*|\ball genders?\b", re.I)
_LOC_TAIL_RE = re.compile(r"\s*[-\u2013\u2014|,]\s*(?:" + _LOC + r")\b[^-\u2013\u2014|]*$", re.I)


def norm_title(title):
    """Role identity for duplicate checks: drops (m/f/d) style gender marks, bracketed
    gender/location notes and a trailing '- Berlin' style location."""
    t = re.sub(r"[(\[]([^)\]]*)[)\]]",
               lambda m: " " if _GENDER_RE.search(m.group(1)) or re.search(r"\b(?:" + _LOC + r")\b", m.group(1), re.I)
               else m.group(0), title or "")
    t = _GENDER_RE.sub(" ", t)
    return _slug(_LOC_TAIL_RE.sub("", t.strip()))


def role_submitted(con, company, title, days=90):
    """Was this (company, role) already submitted in the window, under any job id?"""
    key = (_slug(company), norm_title(title))
    return any((_slug(c), norm_title(t)) == key for _j, c, t in _recent_submitted(con, days))


def _company_ids(con, company, days, exclude=None):
    """Job ids at `company` submitted in the window, plus ones a live worker has claimed
    right now (a claim is the only thing visible before the application row exists)."""
    s = _slug(company)
    ids = {j for j, c, _t in _recent_submitted(con, days) if _slug(c) == s}
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for jid, c, tok in con.execute(
            "SELECT id, company, claim_token FROM jobs WHERE claim_token IS NOT NULL AND claimed_at >= ?", (stale,)):
        pid = _token_pid(tok)
        if _slug(c) == s and not (pid > 0 and not _pid_alive(pid)):
            ids.add(jid)
    ids.discard(exclude)
    return ids


def save_cv_match(con, job_id, key, pct):
    """Caller commits: the apply gate writes one row per job, then commits once."""
    con.execute("UPDATE jobs SET cv_match=?, cv_match_key=? WHERE id=?", (pct, key, job_id))


def live_cv_references(con, cv_name):
    return con.execute(
        "SELECT COUNT(*) FROM jobs WHERE cv=? AND hidden=0 AND dead_listing=0",
        (cv_name,),
    ).fetchone()[0]


def reassign_cv(con, old_name, new_name):
    cur = con.execute("UPDATE jobs SET cv=? WHERE cv=?", (new_name, old_name))
    con.commit()
    return cur.rowcount

def claim_job(con, job_id, token, stale_seconds=3600, company_cap=None, window_days=90):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        con.execute("BEGIN IMMEDIATE")
        held = con.execute("SELECT claim_token FROM jobs WHERE id=?", (job_id,)).fetchone()
        if held and held[0]:
            pid = _token_pid(held[0])
            if pid > 0 and not _pid_alive(pid):
                con.execute("UPDATE jobs SET claim_token=NULL, claimed_at=NULL WHERE id=?", (job_id,))
        if company_cap:
            # under the write lock, so parallel workers cannot both pass for one company
            co = con.execute("SELECT company FROM jobs WHERE id=?", (job_id,)).fetchone()
            if co and len(_company_ids(con, co[0], window_days, exclude=job_id)) >= company_cap:
                con.rollback()
                return False
        cur = con.execute(
            """UPDATE jobs SET claim_token=?, claimed_at=? WHERE id=?
               AND dead_listing=0 AND hidden=0
               AND (claim_token IS NULL OR claimed_at < ?)
                AND coalesce(status,'') NOT IN ('pending','pending_confirmation')
                AND NOT EXISTS (SELECT 1 FROM applications WHERE job_id=? AND
                    (submitted=1 OR needs_manual=1 OR (coalesce(dry_run,0)=0 AND manual_reason LIKE '%confirm%')))""",
            (token, now, job_id, stale, job_id),
        )
        con.commit()
        return cur.rowcount == 1
    except Exception:
        con.rollback()
        raise

def release_job(con, job_id, token):
    con.execute("UPDATE jobs SET claim_token=NULL, claimed_at=NULL WHERE id=? AND claim_token=?", (job_id, token))
    con.commit()


def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _token_pid(token):
    try:
        return int(str(token).split("-", 1)[0])
    except Exception:
        return -1


def acquire_run_lock(con, name, token, stale_seconds=7200):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        con.execute("BEGIN IMMEDIATE")
        held = con.execute("SELECT token, acquired_at FROM run_locks WHERE name=?", (name,)).fetchone()
        if held:
            pid = _token_pid(held[0])
            # age alone must not free a lock whose process is still running \u2014 a long
            # single-worker batch outlives stale_seconds and got a second run beside it
            if (not _pid_alive(pid)) if pid > 0 else held[1] < stale:
                con.execute("DELETE FROM run_locks WHERE name=? AND token=?", (name, held[0]))
        cur = con.execute(
            "INSERT OR IGNORE INTO run_locks (name, token, acquired_at) VALUES (?, ?, ?)",
            (name, token, now),
        )
        con.commit()
        return cur.rowcount == 1
    except Exception:
        con.rollback()
        raise


def release_run_lock(con, name, token):
    con.execute("DELETE FROM run_locks WHERE name=? AND token=?", (name, token))
    con.commit()


def run_is_active(con):
    return any(_pid_alive(_token_pid(row[0])) for row in con.execute(
        "SELECT token FROM run_locks WHERE name IN ('autopilot', 'autopilot-discover')"))


def all_jobs(con):
    return [dict(r) for r in con.execute("SELECT * FROM jobs").fetchall()]


def applications_with_job(con, limit=15):
    rows = con.execute(
        """SELECT a.*, j.company, j.title FROM applications a
           LEFT JOIN jobs j ON j.id = a.job_id
           ORDER BY a.attempt_id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_dead_if_missing(con, source, seen_ids, exclude_companies=()):
    """A vendor board fetch returns the company's FULL current listing every
    run \u2014 any previously-stored job from that source absent from the fresh
    fetch has been delisted (and vice versa: reappearing ids are un-marked).
    No per-job page visit needed. `exclude_companies` skips boards whose fetch
    failed this run (a network hiccup must not look like a mass delisting)."""
    exclude = {c.lower() for c in exclude_companies}
    rows = con.execute("SELECT id, company, dead_listing FROM jobs WHERE source=?", (source,)).fetchall()
    # substring match: the board-slug guess ("wolt") often isn't the exact stored
    # company name (the board's own feed can say "Wolt - English") \u2014 a straight
    # equality check missed that and wrongly nuked 140 real jobs on a failed fetch.
    rows = [r for r in rows if not any(x in (r["company"] or "").lower() for x in exclude)]
    newly_dead = [r["id"] for r in rows if r["id"] not in seen_ids and not r["dead_listing"]]
    revived = [r["id"] for r in rows if r["id"] in seen_ids and r["dead_listing"]]
    for jid in newly_dead:
        con.execute("UPDATE jobs SET dead_listing=1 WHERE id=?", (jid,))
    for jid in revived:
        con.execute("UPDATE jobs SET dead_listing=0 WHERE id=?", (jid,))
    if newly_dead or revived: con.commit()
    return len(newly_dead)


def set_dead_listing(con, job_id, dead=True):
    con.execute("UPDATE jobs SET dead_listing=? WHERE id=?", (1 if dead else 0, job_id))
    con.commit()


def recheck_candidates(con, min_score, limit):
    """Jobs due for a liveness re-visit: not already known-dead/hidden, above the
    score floor, oldest-checked first. Excludes 'vendor' \u2014 mark_dead_if_missing
    already re-verifies those in full on every discover() run."""
    rows = con.execute(
        """SELECT id, url, company, title FROM jobs
           WHERE dead_listing=0 AND hidden=0 AND source != 'vendor' AND score >= ?
           ORDER BY updated_at ASC LIMIT ?""",
        (min_score, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def hide_job(con, job_id):
    """User manually removed this listing from the queue. Separate from
    dead_listing (which the scraper owns and can revive) \u2014 discover() must
    never un-hide something the user deliberately hid."""
    con.execute("UPDATE jobs SET hidden=1 WHERE id=?", (job_id,))
    con.commit()


def record_source_run(con, source, persona, started_at, finished_at, found=0, stored=0, queued_after=0, errors=0, commit=True):
    """One row per discover pass of one source for one persona (`sources` / `report --json`)."""
    con.execute("INSERT INTO source_runs (source, persona, started_at, finished_at, found, stored, queued_after, errors) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (source, persona, started_at, finished_at, int(found), int(stored), int(queued_after), int(errors)))
    if commit:
        con.commit()


def empty_source_row(src):
    return {"source": src, "runs": 0, "last_run": None, "last_found": 0, "last_stored": 0, "last_errors": 0,
            "found_total": 0, "stored_total": 0, "jobs": 0, "queued": 0, "submitted": 0, "interviews": 0}


def source_stats(con):
    """{source: {...}} over source_runs plus jobs/applications/outcomes by jobs.source.
    last_* describe the newest run (all personas of it summed); submitted / interviews are
    confirmed applications and interview+ outcomes (interview, offer, hired) of that source's jobs."""
    out = {}

    def row(src):
        return out.setdefault(src, empty_source_row(src))
    for r in con.execute("SELECT source, count(*) n, max(finished_at) last, sum(found) f, sum(stored) s "
                         "FROM source_runs GROUP BY source"):
        d = row(r["source"]); d.update(runs=r["n"], last_run=r["last"], found_total=r["f"] or 0, stored_total=r["s"] or 0)
    for src, d in out.items():   # newest invocation: one discover run writes all its persona rows with the same finished_at
        r = con.execute("SELECT sum(found) f, sum(stored) s, sum(errors) e FROM source_runs "
                        "WHERE source=? AND finished_at = (SELECT max(finished_at) FROM source_runs WHERE source=?)",
                        (src, src)).fetchone()
        d.update(last_found=r["f"] or 0, last_stored=r["s"] or 0, last_errors=r["e"] or 0)
    for r in con.execute("SELECT coalesce(source,'?') src, count(*) n, sum(status='queued') q FROM jobs GROUP BY 1"):
        d = row(r["src"]); d.update(jobs=r["n"], queued=r["q"] or 0)
    for r in con.execute("SELECT coalesce(j.source,'?') src, count(DISTINCT a.job_id) n FROM applications a "
                         "JOIN jobs j ON j.id=a.job_id WHERE a.submitted=1 GROUP BY 1"):
        row(r["src"])["submitted"] = r["n"]
    for r in con.execute("SELECT coalesce(j.source,'?') src, count(DISTINCT o.job_id) n FROM outcomes o "
                         "JOIN jobs j ON j.id=o.job_id WHERE o.status IN ('interview','offer','hired') GROUP BY 1"):
        row(r["src"])["interviews"] = r["n"]
    return out


def counts(con):
    jobs = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    applied = con.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    return jobs, applied


def persona_counts(con):
    rows = con.execute("SELECT COALESCE(persona, 'appsec') as p, COUNT(*) as c FROM jobs GROUP BY p").fetchall()
    app_rows = con.execute("SELECT COALESCE(persona, 'appsec') as p, COUNT(*) as c FROM applications GROUP BY p").fetchall()
    res = {}
    for r in rows:
        res.setdefault(r["p"], {"jobs": 0, "applied": 0})["jobs"] = r["c"]
    for r in app_rows:
        res.setdefault(r["p"], {"jobs": 0, "applied": 0})["applied"] = r["c"]
    return res


def get_qa_answer(con, key):
    """Look up a cached form-question answer; bumps the reuse counter so hit
    rate is visible (most vacancies repeat the same handful of questions)."""
    row = con.execute("SELECT answer FROM qa_answers WHERE question_key=?", (key,)).fetchone()
    target_key = key
    if row is None and " | options:" in key:
        base_key = key.split(" | options:")[0]
        row = con.execute("SELECT answer FROM qa_answers WHERE question_key=?", (base_key,)).fetchone()
        if row is not None:
            target_key = base_key
    if row is None and "@" in key:
        unscoped = key.split("@", 1)[1]
        row = con.execute("SELECT answer FROM qa_answers WHERE question_key=?", (unscoped,)).fetchone()
        if row is not None:
            target_key = unscoped
        elif " | options:" in unscoped:
            base_unscoped = unscoped.split(" | options:")[0]
            row = con.execute("SELECT answer FROM qa_answers WHERE question_key=?", (base_unscoped,)).fetchone()
            if row is not None:
                target_key = base_unscoped
    if row is None:
        return None
    con.execute("UPDATE qa_answers SET hits = hits + 1 WHERE question_key=?", (target_key,))
    con.commit()
    return row["answer"]


def save_qa_answer(con, key, label, answer, company=""):
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """INSERT INTO qa_answers (question_key, question_label, answer, company, hits, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(question_key) DO UPDATE SET
               answer=excluded.answer, company=excluded.company, updated_at=excluded.updated_at""",
        (key, label, answer, company, now, now),
    )
    clear_answered_pending(con)   # commits



def record_outcome(con, job_id, status, note="", commit=True):
    con.execute(
        "INSERT INTO outcomes (job_id, status, noted_at, note) VALUES (?, ?, ?, ?)",
        (job_id, status, datetime.now(timezone.utc).isoformat(), note),
    )
    if commit: con.commit()


def submitted_jobs(con):
    rows = con.execute(
        """SELECT j.id, j.company, j.title, j.source, j.url,
                  MIN(a.applied_at) applied_at,
                  (SELECT o.status FROM outcomes o WHERE o.job_id = j.id
                   ORDER BY o.outcome_id DESC LIMIT 1) current_status
           FROM jobs j JOIN applications a ON a.job_id = j.id
           WHERE a.submitted = 1
           GROUP BY j.id
           ORDER BY applied_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def find_submitted(con, needle):
    like = f"%{needle.lower()}%"
    rows = con.execute(
        """SELECT DISTINCT j.id, j.company, j.title, j.source
           FROM jobs j JOIN applications a ON a.job_id = j.id
           WHERE a.submitted = 1
             AND (j.id = ? OR LOWER(j.company) LIKE ? OR LOWER(j.title) LIKE ?)
           ORDER BY j.company""",
        (needle, like, like),
    ).fetchall()
    return [dict(r) for r in rows]


def save_outcome_detail(con, d, commit=True):
    """What the mail said, per job and status: auto/manual, the stated reason, how long
    after applying it came, and the link for an invitation. Keeps a `done` flag the user
    set on an invitation they already handled."""
    con.execute(
        """INSERT INTO outcome_details (job_id, status, kind, reason, hours, email_date, subject, sender, link)
           VALUES (:job_id, :status, :kind, :reason, :hours, :email_date, :subject, :sender, :link)
           ON CONFLICT(job_id, status) DO UPDATE SET kind=excluded.kind, reason=excluded.reason,
               hours=excluded.hours, email_date=excluded.email_date, subject=excluded.subject,
               sender=excluded.sender, link=COALESCE(excluded.link, outcome_details.link)""",
        d)
    if commit: con.commit()


def outcome_details(con):
    rows = con.execute(
        """SELECT d.*, j.company, j.title, j.location, j.url, j.description, j.persona, j.source
           FROM outcome_details d JOIN jobs j ON j.id = d.job_id
           ORDER BY d.email_date DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def mark_action_done(con, job_id):
    con.execute("UPDATE outcome_details SET done=1 WHERE job_id=? AND status != 'rejected'", (job_id,))
    con.commit()


def outcome_history(con, job_id):
    rows = con.execute(
        "SELECT status, noted_at, note FROM outcomes WHERE job_id=? ORDER BY outcome_id",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def apply_latency(con):
    rows = con.execute(
        """SELECT j.created_at c, MIN(a.applied_at) ap FROM jobs j
           JOIN applications a ON a.job_id = j.id
           WHERE a.submitted = 1 GROUP BY j.id"""
    ).fetchall()
    days = []
    for r in rows:
        try:
            c = datetime.fromisoformat(r["c"]); ap = datetime.fromisoformat(r["ap"])
            if c.tzinfo is None: c = c.replace(tzinfo=timezone.utc)
            if ap.tzinfo is None: ap = ap.replace(tzinfo=timezone.utc)
            days.append((ap - c).total_seconds() / 86400)
        except Exception:
            pass
    if not days:
        return {"submitted": 0}
    days.sort()
    mid = len(days) // 2
    median = days[mid] if len(days) % 2 else (days[mid - 1] + days[mid]) / 2
    return {
        "submitted": len(days),
        "median_days_to_apply": round(median, 1),
        "within_24h_pct": round(100 * sum(1 for d in days if d < 1) / len(days), 1),
        "within_72h_pct": round(100 * sum(1 for d in days if d < 3) / len(days), 1),
        "over_14d_pct": round(100 * sum(1 for d in days if d > 14) / len(days), 1),
    }


def outcome_stats(con):
    jobs = submitted_jobs(con)
    total = len(jobs)
    tracked = [j for j in jobs if j["current_status"]]
    positive = [j for j in tracked if j["current_status"] in POSITIVE_STATUSES]
    interviews = [j for j in tracked if j["current_status"] in ("interview", "offer", "hired")]

    by_source = {}
    for j in jobs:
        src = j["source"] or "unknown"
        s = by_source.setdefault(src, {"source": src, "submitted": 0, "replied": 0, "interviews": 0})
        s["submitted"] += 1
        if j["current_status"] in POSITIVE_STATUSES:
            s["replied"] += 1
        if j["current_status"] in ("interview", "offer", "hired"):
            s["interviews"] += 1

    by_status = {}
    for j in tracked:
        by_status[j["current_status"]] = by_status.get(j["current_status"], 0) + 1

    return {
        "submitted": total,
        "tracked": len(tracked),
        "untracked": total - len(tracked),
        "replies": len(positive),
        "interviews": len(interviews),
        "reply_rate": round(100 * len(positive) / len(tracked), 1) if tracked else None,
        "by_status": by_status,
        "by_source": sorted(by_source.values(), key=lambda s: (-s["interviews"], -s["replied"], -s["submitted"])),
    }


def qa_stats(con):
    row = con.execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM qa_answers").fetchone()
    return {"answers": row[0], "total_reuses": row[1]}


def application_stats(con):
    """Aggregates over the FULL application history (not just the recent-N
    shown in the Applied table) \u2014 for the dashboard's Statistics tab."""
    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(submitted),0), COALESCE(SUM(dead_listing),0), COALESCE(AVG(filled_fields),0) FROM applications"
    ).fetchone()
    total, submitted, dead, avg_filled = row
    by_company = con.execute(
        """SELECT j.company, COUNT(*) c, SUM(a.submitted) s FROM applications a
           LEFT JOIN jobs j ON j.id = a.job_id GROUP BY j.company ORDER BY c DESC LIMIT 8"""
    ).fetchall()
    return {
        "total": total, "submitted": submitted, "dead": dead,
        "avg_filled": round(avg_filled, 1),
        "by_company": [{"company": r["company"], "attempts": r["c"], "submitted": r["s"] or 0} for r in by_company],
    }


def list_qa_answers(con):
    """Most-reused first \u2014 the questions worth double-checking, since a wrong
    answer here gets repeated on every future vacancy until corrected."""
    rows = con.execute("SELECT * FROM qa_answers ORDER BY hits DESC, updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def record_pending_question(con, key, label, options, company, job_url, commit=True):
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """INSERT INTO pending_questions (question_key, question_label, options, company, job_url,
               seen, first_seen, last_seen)
           VALUES (?,?,?,?,?,1,?,?)
           ON CONFLICT(question_key) DO UPDATE SET
               seen = seen + 1, last_seen = excluded.last_seen,
               job_url = COALESCE(NULLIF(excluded.job_url,''), job_url),
               company = COALESCE(NULLIF(excluded.company,''), company)""",
        (key, label, json.dumps(list(options or []), ensure_ascii=False), company or "", job_url or "", now, now))
    if commit: con.commit()


def list_pending_questions(con, limit=50):
    rows = con.execute(
        f"""SELECT p.*, d.reason FROM pending_questions p
           LEFT JOIN answer_decisions d ON d.question_key=p.question_key AND d.job_url=p.job_url
           WHERE {_PENDING_UNRESOLVED}
           ORDER BY p.seen DESC, p.last_seen DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def clear_answered_pending(con):
    n = con.execute(
        f"""DELETE FROM pending_questions WHERE question_key IN
           (SELECT p.question_key FROM pending_questions p WHERE NOT ({_PENDING_UNRESOLVED}))""").rowcount
    con.commit()
    return n


_PENDING_UNRESOLVED = """NOT EXISTS (
    SELECT 1 FROM qa_answers a WHERE a.question_key=p.question_key
    AND NOT EXISTS (SELECT 1 FROM answer_decisions d
        WHERE d.question_key=p.question_key AND d.job_url=p.job_url AND d.status='manual'
        AND d.checked_at >= coalesce(a.updated_at, '')))"""


def record_answer_decision(con, decision):
    fields = ("question_key", "job_url", "persona", "question", "company", "source", "method",
              "status", "draft", "answer", "reason", "evidence", "context_key", "cv", "checked_at")
    row = {**decision, "checked_at": datetime.now(timezone.utc).isoformat(),
           "evidence": json.dumps(decision.get("evidence", []), ensure_ascii=False)}
    con.execute("INSERT INTO answer_decisions (" + ",".join(fields) + ") VALUES (" +
                ",".join("?" for _ in fields) + ") ON CONFLICT(question_key,job_url) DO UPDATE SET " +
                ",".join(f"{f}=excluded.{f}" for f in fields[2:]), [row.get(f, "") for f in fields])
    con.commit()


def get_answer_review(con, context_key):
    row = con.execute("SELECT * FROM answer_decisions WHERE context_key=? AND method='cv_review' "
                      "AND status IN ('approved','revised') ORDER BY checked_at DESC LIMIT 1",
                      (context_key,)).fetchone()
    return {**dict(row), "evidence": json.loads(row["evidence"])} if row else None


def list_answer_decisions(con, limit=30):
    rows = con.execute("SELECT * FROM answer_decisions ORDER BY checked_at DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "evidence": json.loads(r["evidence"] or "[]")} for r in rows]


def fix_job_statuses(con):
    """One-off repair: upsert_job used to reset applied jobs to 'queued' on re-discovery.
    Submitted jobs get 'applied' again; 'applied' without any submission goes back to 'queued'."""
    sub = "SELECT job_id FROM applications WHERE submitted=1"
    up = con.execute(f"UPDATE jobs SET status='applied' WHERE coalesce(status,'')<>'applied' AND id IN ({sub})").rowcount
    down = con.execute(f"UPDATE jobs SET status='queued' WHERE status='applied' AND id NOT IN ({sub})").rowcount
    con.commit()
    return up, down


_CREATIVE_RE = re.compile(r"photograph|davinci|premiere pro|after effects|lightroom|photo and video|photo & video|"
                          r"commercial production|tilda\.ws|video ads you", re.I)


def fix_qa_personas(con):
    """One-off repair: creative answers filed under unprefixed (appsec) keys move to
    'creative@<key>' (dropped if that exists); the AppSec certifications answer filed under a
    creative key is deleted so it regenerates. Returns (moved, dropped)."""
    moved = dropped = 0
    for key, answer in con.execute(
            "SELECT question_key, answer FROM qa_answers WHERE question_key NOT LIKE 'creative@%'").fetchall():
        if _CREATIVE_RE.search(answer or "") and not re.search(r"security", answer, re.I):   # mixed answers stay appsec
            n = con.execute("UPDATE OR IGNORE qa_answers SET question_key=? WHERE question_key=?", ("creative@" + key, key)).rowcount
            if n: moved += 1
            else:
                con.execute("DELETE FROM qa_answers WHERE question_key=?", (key,)); dropped += 1
    dropped += con.execute("DELETE FROM qa_answers WHERE question_key LIKE 'creative@%' AND answer LIKE '%Security+%'").rowcount
    con.commit()
    return moved, dropped


def update_qa_answer(con, key, answer):
    con.execute(
        "UPDATE qa_answers SET answer=?, updated_at=? WHERE question_key=?",
        (answer, datetime.now(timezone.utc).isoformat(), key),
    )
    con.commit()


def delete_qa_answer(con, key):
    con.execute("DELETE FROM qa_answers WHERE question_key=?", (key,))
    con.commit()


def insert_lead(con, lead_dict, commit=True):
    now = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO leads (job_id, company, name, role, email, email_source, confidence,
                           linkedin_url, pitch_subject, pitch_body, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        lead_dict.get("job_id"),
        lead_dict.get("company", ""),
        lead_dict.get("name", ""),
        lead_dict.get("role", ""),
        lead_dict.get("email", ""),
        lead_dict.get("email_source", "guessed"),
        float(lead_dict.get("confidence", 0.8)),
        lead_dict.get("linkedin_url", ""),
        lead_dict.get("pitch_subject", ""),
        lead_dict.get("pitch_body", ""),
        lead_dict.get("status", "discovered"),
        lead_dict.get("created_at") or now,
    ))
    if commit:
        con.commit()
    return cur.lastrowid


def get_leads(con, company=None, job_id=None, limit=50):
    if job_id:
        rows = con.execute("SELECT * FROM leads WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)).fetchall()
    elif company:
        rows = con.execute("SELECT * FROM leads WHERE company LIKE ? ORDER BY id DESC LIMIT ?", (f"%{company}%", limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def update_lead_status(con, lead_id, status, commit=True):
    con.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    if commit:
        con.commit()


def is_email_replied(con, message_id):
    if not message_id:
        return False
    row = con.execute("SELECT 1 FROM auto_replies WHERE message_id = ?", (message_id,)).fetchone()
    return row is not None


def log_auto_reply(con, reply_dict, commit=True):
    now = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT OR IGNORE INTO auto_replies (message_id, in_reply_to, recipient, subject, intent, reply_body, sent_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        reply_dict.get("message_id"),
        reply_dict.get("in_reply_to"),
        reply_dict.get("recipient"),
        reply_dict.get("subject"),
        reply_dict.get("intent", "general"),
        reply_dict.get("reply_body", ""),
        reply_dict.get("sent_at") or now,
        reply_dict.get("status", "sent"),
    ))
    if commit:
        con.commit()
    return cur.lastrowid
