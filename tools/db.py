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
    persona TEXT DEFAULT 'appsec'
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
    table — patch older jobs.db files created before these columns existed."""
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
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_persona ON jobs(persona)")

    acols = {r["name"] for r in con.execute("PRAGMA table_info(applications)").fetchall()}
    for col, ddl in (("needs_manual", "needs_manual INTEGER DEFAULT 0"),
                     ("apply_url", "apply_url TEXT"), ("manual_reason", "manual_reason TEXT"),
                     ("persona", "persona TEXT DEFAULT 'appsec'")):
        if col not in acols:
            con.execute(f"ALTER TABLE applications ADD COLUMN {ddl}")
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
               status=excluded.status, cv=excluded.cv,
               salary=COALESCE(excluded.salary, jobs.salary),
               persona=COALESCE(excluded.persona, jobs.persona, 'appsec'),
               updated_at=excluded.created_at""",
        data,
    )
    if commit: con.commit()


_APPLICATION_DEFAULTS = {"dry_run": 0, "review": 0, "submitted": 0, "dead_listing": 0,
                          "filled_fields": 0, "cv_used": None, "cover_used": None, "screenshot": None,
                          "needs_manual": 0, "apply_url": None, "manual_reason": None,
                          "persona": "appsec"}


def insert_application(con, rec, commit=True):
    data = {**_APPLICATION_DEFAULTS, **rec}
    if not rec.get("persona") and rec.get("id"):
        j_p = con.execute("SELECT persona FROM jobs WHERE id=?", (rec["id"],)).fetchone()
        if j_p and j_p[0]:
            data["persona"] = j_p[0]
    con.execute(
        """INSERT INTO applications (job_id, applied_at, dry_run, review, submitted,
               dead_listing, filled_fields, cv_used, cover_used, screenshot,
               needs_manual, apply_url, manual_reason, persona)
           VALUES (:id, :applied_at, :dry_run, :review, :submitted, :dead_listing,
               :filled_fields, :cv_used, :cover_used, :screenshot,
               :needs_manual, :apply_url, :manual_reason, :persona)""",
        data,
    )
    if rec.get("dead_listing"):
        con.execute("UPDATE jobs SET dead_listing=1 WHERE id=?", (rec["id"],))
    if commit: con.commit()


def manual_needed(con):
    """Jobs the bot filled but couldn't auto-submit (Ashby spam-block, captcha, etc.)
    and that haven't since been resolved — the dashboard shows these as alerts with a
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


def skip_ids(con):
    """Job ids to never queue again: already submitted, a delisted page, or a
    spam-block verdict. A spam-block ("site blocked this automated session as
    spam") is the ATS's own definitive anti-abuse call against the WHOLE
    automated session, not a transient failure — retrying it automatically can
    never succeed (confirmed live: repeat Ashby postings kept re-surfacing in
    every `apply` run and getting spam-blocked again, burning attempts that
    could have gone to fresh postings, instead of the one-time "finish in your
    own browser" note it already got). Other needs_manual reasons (captcha,
    "submit didn't confirm") stay eligible — those genuinely can succeed on a
    later attempt, especially after a bug fix.
    A stall is NOT one of them: the watchdog fires on the run's own timeout, which
    it also fires while a human is mid-review, so blacklisting on it permanently
    removed the exact job the user was working on. A SECOND stall is: a posting that
    always hangs otherwise burns a full vacancy timeout in every run."""
    rows = con.execute(
        """SELECT job_id FROM applications WHERE submitted=1
           UNION SELECT id FROM jobs WHERE dead_listing=1
           UNION SELECT job_id FROM applications WHERE needs_manual=1 AND applied_at > datetime('now', '-7 days') AND (manual_reason IS NULL OR manual_reason NOT LIKE '%stalled the run%')
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%blocked this automated session as spam%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%signing in with a Google account%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%creating or signing in to a site account%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%posting requires pre-existing eligibility%'
           UNION SELECT job_id FROM applications WHERE manual_reason LIKE '%stalled the run%'
                 GROUP BY job_id HAVING COUNT(*) >= 2"""
    ).fetchall()
    return {r[0] for r in rows}


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


def eligible_jobs(con, min_score, persona=None):
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

    rows = con.execute(
        f"""SELECT *, score - ({STALENESS_PENALTY}) + ({TIER_BOOST}) AS priority FROM jobs
           WHERE score >= ? AND dead_listing=0 AND hidden=0
           AND (claim_token IS NULL OR claimed_at < ?)
           {where_extra}
           ORDER BY priority DESC, score DESC""", tuple(params)
    ).fetchall()
    return [dict(r) for r in rows if r["id"] not in skip]


def _slug(s):
    # same as autopilot.slug — importing autopilot here would be circular
    return re.sub(r"[^a-z0-9]+", "-", (s or "job").lower()).strip("-")[:80] or "job"


def recent_company_counts(con, days):
    """{company_slug: submitted applications in the last `days` days} — the
    per-run company cap has no memory, so repeated runs kept hitting the same
    company (Ashby: ~5 per 180 days, spam-blocks bursts). applied_at is Python
    isoformat, so the cut-off is too: SQLite datetime('now') uses a different
    format and string comparison against it is wrong."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = con.execute(
        """SELECT j.company, COUNT(DISTINCT a.job_id) FROM applications a
           JOIN jobs j ON j.id = a.job_id
           WHERE a.submitted = 1 AND a.applied_at >= ?
           GROUP BY j.company""", (since,)).fetchall()
    out = {}
    for company, n in rows:
        out[_slug(company)] = out.get(_slug(company), 0) + n
    return out


def live_cv_references(con, cv_name):
    return con.execute(
        "SELECT COUNT(*) FROM jobs WHERE cv=? AND hidden=0 AND dead_listing=0",
        (cv_name,),
    ).fetchone()[0]


def reassign_cv(con, old_name, new_name):
    cur = con.execute("UPDATE jobs SET cv=? WHERE cv=?", (new_name, old_name))
    con.commit()
    return cur.rowcount

def claim_job(con, job_id, token, stale_seconds=3600):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        con.execute("BEGIN IMMEDIATE")
        held = con.execute("SELECT claim_token FROM jobs WHERE id=?", (job_id,)).fetchone()
        if held and held[0]:
            pid = _token_pid(held[0])
            if pid > 0 and not _pid_alive(pid):
                con.execute("UPDATE jobs SET claim_token=NULL, claimed_at=NULL WHERE id=?", (job_id,))
        cur = con.execute(
            """UPDATE jobs SET claim_token=?, claimed_at=? WHERE id=?
               AND dead_listing=0 AND hidden=0
               AND (claim_token IS NULL OR claimed_at < ?)
               AND NOT EXISTS (SELECT 1 FROM applications WHERE job_id=? AND submitted=1)""",
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
            # age alone must not free a lock whose process is still running — a long
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
    run — any previously-stored job from that source absent from the fresh
    fetch has been delisted (and vice versa: reappearing ids are un-marked).
    No per-job page visit needed. `exclude_companies` skips boards whose fetch
    failed this run (a network hiccup must not look like a mass delisting)."""
    exclude = {c.lower() for c in exclude_companies}
    rows = con.execute("SELECT id, company, dead_listing FROM jobs WHERE source=?", (source,)).fetchall()
    # substring match: the board-slug guess ("wolt") often isn't the exact stored
    # company name (the board's own feed can say "Wolt - English") — a straight
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
    score floor, oldest-checked first. Excludes 'vendor' — mark_dead_if_missing
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
    dead_listing (which the scraper owns and can revive) — discover() must
    never un-hide something the user deliberately hid."""
    con.execute("UPDATE jobs SET hidden=1 WHERE id=?", (job_id,))
    con.commit()


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
    if row is None:
        return None
    con.execute("UPDATE qa_answers SET hits = hits + 1 WHERE question_key=?", (key,))
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
    con.commit()


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
    shown in the Applied table) — for the dashboard's Statistics tab."""
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
    """Most-reused first — the questions worth double-checking, since a wrong
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
               company = COALESCE(NULLIF(excluded.company,''), company)""",
        (key, label, json.dumps(list(options or []), ensure_ascii=False), company or "", job_url or "", now, now))
    if commit: con.commit()


def list_pending_questions(con, limit=50):
    rows = con.execute(
        """SELECT p.* FROM pending_questions p
           WHERE NOT EXISTS (SELECT 1 FROM qa_answers a WHERE a.question_key = p.question_key)
           ORDER BY p.seen DESC, p.last_seen DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def clear_answered_pending(con):
    n = con.execute(
        """DELETE FROM pending_questions
           WHERE question_key IN (SELECT question_key FROM qa_answers)""").rowcount
    con.commit()
    return n


def update_qa_answer(con, key, answer):
    con.execute(
        "UPDATE qa_answers SET answer=?, updated_at=? WHERE question_key=?",
        (answer, datetime.now(timezone.utc).isoformat(), key),
    )
    con.commit()


def delete_qa_answer(con, key):
    con.execute("DELETE FROM qa_answers WHERE question_key=?", (key,))
    con.commit()
