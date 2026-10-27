"""Goal B apply gate: CV-match threshold, 1-per-company/90-day cap, freshness cutoff.
Run: PYTHONIOENCODING=utf-8 PYTHONPATH=tools python tests/test_apply_gate.py
Drives the real apply() queue build on a temp DB and stops before any browser starts."""
import argparse, pathlib, tempfile
from datetime import datetime, timedelta, timezone

import db

import autopilot as A

PATCHED = ("extract_cv_text", "pick_cv", "_cv_names_candidate", "_strict_discovery_reason",
           "_start_stall_watchdog", "apply_one", "read_json", "write_progress", "cv_match_pct")


def demo():
    saved = {k: getattr(A, k) for k in PATCHED}, db.DB_PATH
    tmp = pathlib.Path(tempfile.mkdtemp())
    db.DB_PATH = tmp / "jobs.db"
    try:
        _run(tmp)
    finally:
        for k, v in saved[0].items():
            setattr(A, k, v)
        db.DB_PATH = saved[1]


def _run(tmp):
    NOW = datetime.now(timezone.utc)
    CV = tmp / "appsec.pdf"
    CV.write_bytes(b"fixture")
    CV_TEXT = ("application security engineer burp suite owasp threat modeling kubernetes docker "
               "python sast dast secure code review api security penetration testing aws")
    GOOD = ("We need an application security engineer: burp suite, owasp, threat modeling, "
            "kubernetes, docker, python, sast, dast, secure code review and api security on aws. ") * 2
    BAD = ("Lead our payroll accounting close, reconcile ledgers in SAP, prepare statutory "
           "financial statements, coordinate auditors, manage invoices and treasury forecasts. ") * 2

    A.extract_cv_text = lambda *a, **k: CV_TEXT
    A.pick_cv = lambda *a, **k: str(CV)
    A._cv_names_candidate = lambda *a, **k: True
    A._strict_discovery_reason = lambda *a, **k: None
    A._start_stall_watchdog = lambda *a, **k: None
    A.apply_one = lambda *a, **k: (_ for _ in ()).throw(AssertionError("preview must not apply"))
    CFG = {"profile": {}, "search": {}, "llm": {"enabled": False}, "personas": {},
           "email": {"app_password": "x"},
           "apply": {"max_per_run": 50, "min_cv_match": 30, "max_posting_age_days": 5,
                     "max_per_company_window": 1, "company_window_days": 90, "reject_cooldown_count": 1}}
    A.read_json = lambda *a, **k: CFG

    class Stop(Exception): pass
    seen = {}
    def progress(done, total, label=""):
        seen["n"] = total
        raise Stop()
    A.write_progress = progress

    con = db.connect()
    def job(jid, company, desc, age_days=1, title="Application Security Engineer"):
        db.upsert_job(con, {"id": jid, "source": "greenhouse", "keyword": "", "title": title,
                            "company": company, "location": "Remote", "description": desc,
                            "url": f"https://job-boards.greenhouse.io/{company.lower()}/jobs/{abs(hash(jid)) % 10**8}",
                            "score": 50, "cv": "appsec.pdf", "status": "queued",
                            "created_at": (NOW - timedelta(days=age_days)).isoformat()})

    def queue_size():
        seen.clear()
        try:
            A.apply(argparse.Namespace(dry_run=True, submit=False, review=False, max=50, workers=1, job=None,
                                       persona="all", location=None, country=None, ats_only=False, show=False))
        except Stop:
            pass
        return seen.get("n", 0)

    # --- unit: match %, description floor, persona override, cache, age ---
    assert A.cv_match_pct({"title": "x", "description": GOOD}, "cv") >= 30
    assert A.cv_match_pct({"title": "x", "description": BAD}, "cv") < 30
    assert A.cv_match_pct({"title": "AppSec", "description": "Auto-harvested via greenhouse board public API."}, "cv") is None
    assert A.min_cv_match_for({"persona": "appsec"}, CFG) == 30
    assert A.min_cv_match_for({"persona": "creative"}, {**CFG, "personas": {"creative": {"min_cv_match": 5}}}) == 5
    assert A.min_cv_match_for({}, {"apply": {}}) == 15, "code default"
    assert A.posting_age_days({"created_at": (NOW - timedelta(days=7)).isoformat()}) > 6.9
    assert A.posting_age_days({"url": "https://jooble.org/desc/1?jobAge=2", "created_at": NOW.isoformat()}) == 2, \
        "a board-reported posting age wins over first-seen"

    job("good", "Acme", GOOD)
    calls = []
    _orig = A.cv_match_pct
    A.cv_match_pct = lambda j, cv: calls.append(1) or _orig(j, cv)
    row = dict(con.execute("SELECT * FROM jobs WHERE id='good'").fetchone())
    first = A.cached_cv_match(con, row, str(CV)); con.commit()
    row = dict(con.execute("SELECT * FROM jobs WHERE id='good'").fetchone())
    assert row["cv_match"] == first and A.cached_cv_match(con, row, str(CV)) == first and len(calls) == 1, \
        "second lookup must come from the job row"
    row["description"] = BAD
    assert A.cached_cv_match(con, row, str(CV)) < 30 and len(calls) == 2, "edited posting recomputes"
    A.cv_match_pct = _orig

    # --- end to end through apply()'s queue ---
    assert queue_size() == 1, "fresh + matching job is queued"

    job("low", "LowCo", BAD)
    job("nodesc", "NoDescCo", "Auto-harvested via greenhouse board public API.")
    job("old", "OldCo", GOOD, age_days=6)
    assert queue_size() == 1, "low match, missing description and a 6-day-old posting are all skipped"

    CFG["apply"]["max_posting_age_days"] = 10
    assert queue_size() == 2, "freshness cutoff is configurable"
    CFG["apply"]["max_posting_age_days"] = 5
    CFG["apply"]["min_cv_match"] = 0
    assert queue_size() == 3, "min_cv_match 0 disables the match gate (low + nodesc back, old still stale)"
    CFG["apply"]["min_cv_match"] = 30

    # two different roles at one company: only one per run
    job("good2", "Acme", GOOD, title="Product Security Engineer")
    assert queue_size() == 1, "never two roles at one company in one run"

    # a submission 60 days ago still blocks the company (90-day window), one at 100 days does not
    job("past", "Acme", GOOD, age_days=60, title="Old role")
    db.insert_application(con, {"id": "past", "submitted": 1, "applied_at": (NOW - timedelta(days=60)).isoformat()})
    assert queue_size() == 0, "company already applied to within 90 days"
    con.execute("UPDATE applications SET applied_at=? WHERE job_id='past'", ((NOW - timedelta(days=100)).isoformat(),))
    con.commit()
    assert queue_size() == 1, "outside the 90-day window the company is open again"

    # any rejection within 90 days pauses the company
    db.record_outcome(con, "past", "rejected")
    assert queue_size() == 0, "one rejection pauses the company (reject_cooldown_count=1)"



test_apply_gate = demo

def test_demo():
    demo()


if __name__ == "__main__":
    demo()
    print("apply gate self-check OK")
