import os
import tempfile
from pathlib import Path

import db

with tempfile.TemporaryDirectory() as td:
    previous = db.DB_PATH
    db.DB_PATH = Path(td) / "jobs.db"
    try:
        first = db.connect()
        second = db.connect()
        assert db.acquire_run_lock(first, "autopilot", "first")
        assert not db.acquire_run_lock(second, "autopilot", "second")
        db.release_run_lock(first, "autopilot", "first")
        assert db.acquire_run_lock(second, "autopilot", "second")

        db.release_run_lock(second, "autopilot", "second")
        assert db.acquire_run_lock(first, "autopilot", "999999-1")
        assert db.acquire_run_lock(second, "autopilot", f"{os.getpid()}-2"), \
            "a lock left behind by a killed run must be taken over, not block every later run"
        assert not db.acquire_run_lock(first, "autopilot", f"{os.getpid()}-3"), \
            "a lock held by a live process stays held"
        second.execute("UPDATE run_locks SET acquired_at='2000-01-01T00:00:00+00:00'")
        second.commit()
        assert not db.acquire_run_lock(first, "autopilot", "999999-4"), \
            "a long run must keep its lock while its process is alive, however old"

        first.close()
        second.close()
    finally:
        db.DB_PATH = previous

print("run lock self-check OK")
