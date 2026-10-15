import os
import sqlite3

import db


def test_active_run_ignores_dead_locks_and_tracks_discovery():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE run_locks (name TEXT, token TEXT)")
    try:
        assert not db.run_is_active(con)
        con.execute("INSERT INTO run_locks VALUES ('autopilot', '99999999-1')")
        assert not db.run_is_active(con)
        con.execute("INSERT INTO run_locks VALUES ('autopilot-discover', ?)", (f"{os.getpid()}-1",))
        assert db.run_is_active(con)
    finally:
        con.close()
