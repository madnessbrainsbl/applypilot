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
    # Invariant: a job whose failure was purely a watchdog stall must NOT be in skip_ids
    test_id = "__test_stall_watchdog_inv__"
    con.execute("INSERT OR REPLACE INTO jobs (id, company, title, url) VALUES (?, 'TestCo', 'SecEng', 'http://example.com/test')", (test_id,))
    con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, manual_reason, submitted) VALUES (?, datetime('now'), 1, 'stalled the run at step fill form after 180s', 0)", (test_id,))
    try:
        skip = db.skip_ids(con)
        assert test_id in skip, "an unconfirmed timeout must require review before retrying"
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
