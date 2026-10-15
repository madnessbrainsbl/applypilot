import tempfile, pathlib, sqlite3
from datetime import datetime, timezone
import db
import autopilot as A

def test_prune():
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    shots_dir = tmp_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    db_file = tmp_dir / "jobs.db"
    
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS applications (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            applied_at TEXT,
            submitted INTEGER DEFAULT 0,
            needs_manual INTEGER DEFAULT 0,
            screenshot TEXT
        );
    """)
    
    # 1. Needs manual file (MUST KEEP)
    f_manual = shots_dir / "manual_job.png"
    f_manual.write_text("dummy")
    con.execute("INSERT INTO applications (job_id, applied_at, needs_manual, screenshot) VALUES (?, ?, 1, ?)",
                ("manual_1", datetime.now(timezone.utc).isoformat(), str(f_manual)))
    
    # 2. Submitted file (MUST KEEP)
    f_sub = shots_dir / "submitted_job.png"
    f_sub.write_text("dummy")
    con.execute("INSERT INTO applications (job_id, applied_at, submitted, screenshot) VALUES (?, ?, 1, ?)",
                ("sub_1", datetime.now(timezone.utc).isoformat(), str(f_sub)))
                
    # 3. Orphaned file older than 1 hour (MUST DELETE)
    f_orphan = shots_dir / "orphan.png"
    f_orphan.write_text("dummy")
    import os, time
    old_time = time.time() - 7200
    os.utime(f_orphan, (old_time, old_time))
    
    con.commit()
    
    # Temporarily monkeypatch SHOTS
    orig_shots = A.SHOTS
    try:
        A.SHOTS = shots_dir
        res = A.prune_screenshots(con, keep_recent=2, keep_needs_manual=True)
        assert res["deleted"] == 1, f"Expected 1 deleted, got {res['deleted']}"
        assert f_manual.exists(), "Manual screenshot must be kept"
        assert f_sub.exists(), "Submitted screenshot must be kept"
        assert not f_orphan.exists(), "Orphaned screenshot must be deleted"
    finally:
        A.SHOTS = orig_shots
        con.close()
    print("test_prune_screenshots passed OK!")

if __name__ == "__main__":
    test_prune()
