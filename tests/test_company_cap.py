"""Per-company cap enforced atomically at claim time, role-twin dedup, status/QA repairs."""
import os
import threading
from datetime import datetime, timezone

import pytest

import db


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    c = db.connect()
    yield c
    c.close()


NOW = datetime.now(timezone.utc).isoformat()


def add(con, jid, company="Acme", title=None, status="queued"):
    db.upsert_job(con, {"id": jid, "source": "v", "keyword": "", "title": title or f"Role {jid}", "company": company,
                        "location": "", "url": f"https://x/{jid}", "description": "d", "score": 50, "cv": None,
                        "status": status, "created_at": NOW})


def test_norm_title():
    n = db.norm_title
    assert n("Security Engineer (m/f/d)") == n("Security Engineer - m/f/d") == n("Security Engineer - Berlin") == "security-engineer"
    assert n("Engineering Manager- Business Performance (x/f/m)") == "engineering-manager-business-performance"
    assert n("Security Engineer, Berlin (Hybrid)") == "security-engineer"
    assert n("Site Reliability Engineer - Data Platform") != n("Site Reliability Engineer - Observability")
    assert n("Software Engineer (Java)") != n("Software Engineer (Go)")
    assert n("Business Analyst") == "business-analyst"          # 'us' inside a word is not a location


def test_claim_enforces_company_cap(con):
    me = f"{os.getpid()}-t"
    for i in range(3):
        add(con, f"a{i}")
    add(con, "b0", company="Beta")
    assert db.claim_job(con, "a0", me, company_cap=1)
    assert not db.claim_job(con, "a1", me + "2", company_cap=1), "a claim in flight counts toward the cap"
    assert db.claim_job(con, "b0", me + "3", company_cap=1), "other companies are unaffected"
    db.release_job(con, "a0", me)
    assert db.claim_job(con, "a1", me + "2", company_cap=1), "released without a submission frees the slot"
    db.release_job(con, "a1", me + "2")
    db.insert_application(con, {"id": "a2", "submitted": 1, "applied_at": datetime.now(timezone.utc).isoformat()})
    assert not db.claim_job(con, "a0", me, company_cap=1), "a submission inside the window blocks every sibling"
    assert db.claim_job(con, "a0", me, company_cap=2), "cap comes from config, not a constant"


def test_parallel_claims_never_exceed_cap(con):
    for i in range(8):
        add(con, f"p{i}", company="Parallel Inc")
    won = []

    def worker(i):
        c = db.connect()
        try:
            if db.claim_job(c, f"p{i}", f"{os.getpid()}-w{i}", company_cap=1):
                won.append(i)
        finally:
            c.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(won) == 1, won


def test_role_twin_under_other_job_id(con):
    add(con, "r1", "Acme", "Security Engineer (m/f/d)")
    add(con, "r2", "ACME", "Security Engineer - Berlin")
    add(con, "r3", "Acme", "Security Engineer - Payments")
    db.insert_application(con, {"id": "r1", "submitted": 1, "applied_at": datetime.now(timezone.utc).isoformat()})
    assert db.role_submitted(con, "ACME", "Security Engineer - Berlin")
    assert not db.role_submitted(con, "Acme", "Security Engineer - Payments")
    assert not db.role_submitted(con, "Other", "Security Engineer")


def test_rediscovery_keeps_status_and_repair(con):
    add(con, "s1", status="applied")
    add(con, "s1", status="queued")                   # re-discovery
    assert con.execute("SELECT status FROM jobs WHERE id='s1'").fetchone()[0] == "applied"
    add(con, "s2")
    db.insert_application(con, {"id": "s2", "submitted": 1, "applied_at": NOW})
    con.execute("UPDATE jobs SET status='queued' WHERE id='s2'")            # damage from the old upsert
    add(con, "s3", status="applied")                                         # 'applied' with no submission
    assert db.fix_job_statuses(con) == (1, 2)       # s2 up; s1 and s3 have no submission
    st = dict(con.execute("SELECT id, status FROM jobs").fetchall())
    assert st["s2"] == "applied" and st["s3"] == "queued"


def test_qa_repair_and_pending_cleanup(con):
    db.save_qa_answer(con, "what editing tools do you use?", "q", "I use DaVinci Resolve and Premiere Pro")
    db.save_qa_answer(con, "portfolio", "q", "https://github.com/x")
    db.save_qa_answer(con, "creative@certifications?", "q", "Security+, CompTIA Security+")
    assert db.fix_qa_personas(con) == (1, 1)
    keys = {r[0] for r in con.execute("SELECT question_key FROM qa_answers")}
    assert keys == {"creative@what editing tools do you use?", "portfolio"}
    db.record_pending_question(con, "portfolio", "Portfolio", [], "Acme", "u")
    db.record_pending_question(con, "other", "Other", [], "Acme", "u")
    db.save_qa_answer(con, "portfolio", "q", "https://github.com/x")
    assert [p["question_key"] for p in db.list_pending_questions(con)] == ["other"]
    assert con.execute("SELECT count(*) FROM pending_questions").fetchone()[0] == 1


def test_eligible_without_description_and_indexes(con):
    add(con, "e1")
    assert "description" in db.eligible_jobs(con, 0)[0]
    assert "description" not in db.eligible_jobs(con, 0, with_description=False)[0]
    idx = {r[1] for r in con.execute("SELECT * FROM sqlite_master WHERE type='index'")}
    assert {"idx_apps_submitted", "idx_jobs_status"} <= idx
