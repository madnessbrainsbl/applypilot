import autopilot as A
import db


def demo():
    assert A._stall_state(0, 60, 180) == "ok"
    assert A._stall_state(59.9, 60, 180) == "ok"
    assert A._stall_state(60, 60, 180) == "warn"
    assert A._stall_state(179, 60, 180) == "warn"
    assert A._stall_state(180, 60, 180) == "kill", "a vacancy must never be allowed to hang forever"
    assert A._stall_state(1384, 60, 180) == "kill", "the 23-minute Workday hang must be caught"

    cfg = A.read_json(A.CFG)
    assert cfg["apply"]["vacancy_timeout_seconds"] > 0
    assert cfg["apply"]["manual_wait_seconds"] > 0

    con = db.connect()
    # An unconfirmed timeout needs human review before another dispatch.
    test_id = "__test_stall_watchdog_inv__"
    con.execute("INSERT OR REPLACE INTO jobs (id, company, title, url) VALUES (?, 'TestCo', 'SecEng', 'http://example.com/test')", (test_id,))
    con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, manual_reason, submitted) VALUES (?, datetime('now'), 1, 'stalled the run at step fill form after 180s', 0)", (test_id,))
    try:
        skip = db.skip_ids(con)
        assert test_id in skip, "a timeout must not authorize repeating an unconfirmed attempt"
        con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, manual_reason, submitted) VALUES (?, datetime('now'), 1, 'stalled the run at step fill form after 180s', 0)", (test_id,))
        assert test_id in db.skip_ids(con), "a posting that stalls twice must stop eating a timeout every run"
    finally:
        con.execute("DELETE FROM applications WHERE job_id=?", (test_id,))
        con.execute("DELETE FROM jobs WHERE id=?", (test_id,))
        con.commit()
        con.close()
    print("stall watchdog self-check OK")


if __name__ == "__main__":
    demo()


def test_watchdog_manual_attempt_is_not_requeued(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    demo()


def test_old_manual_records_stay_excluded_and_fresh_jobs_remain_eligible(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    with db.connect() as con:
        for job_id in ("old-manual", "confirmed", "fresh"):
            con.execute("INSERT INTO jobs (id, score, hidden, dead_listing, persona) VALUES (?, 50, 0, 0, 'appsec')", (job_id,))
        con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, manual_reason, submitted) VALUES ('old-manual', '2020-01-01T00:00:00+00:00', 1, 'submit did not confirm', 0)")
        con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, submitted) VALUES ('confirmed', '2020-01-01T00:00:00+00:00', 0, 1)")
        con.commit()
        assert {j['id'] for j in db.eligible_jobs(con, 0, 'appsec')} == {"fresh"}
        assert con.execute("SELECT needs_manual, submitted FROM applications WHERE job_id='old-manual'").fetchone()[:] == (1, 0)
