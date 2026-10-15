import os
import sqlite3

import autopilot as A


def test_prune_respects_rolling_limit_and_leaves_unrelated_temp(tmp_path, monkeypatch):
    shots = tmp_path / "autopilot" / "screenshots"
    shots.mkdir(parents=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "SHOTS", shots)
    monkeypatch.setattr(A.tempfile, "gettempdir", lambda: str(temp))
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE applications (screenshot TEXT)")
    for i in range(15):
        shot = shots / f"{i}.png"
        shot.write_bytes(b"test")
        os.utime(shot, (i, i))
        con.execute("INSERT INTO applications VALUES (?)", (str(shot.relative_to(tmp_path)),))
    unrelated = temp / "playwright-other-project"
    unrelated.mkdir()
    stale = temp / f"autopilot-cover-{os.getpid()}-test.pdf"
    stale.write_bytes(b"test")
    other_session = temp / "autopilot-cover-99999999-other.pdf"
    other_session.write_bytes(b"other session")
    try:
        result = A.prune_screenshots(con, keep_recent=20)
        assert result["deleted"] == 5
        assert {p.name for p in shots.iterdir()} == {f"{i}.png" for i in range(5, 15)}
        assert con.execute("SELECT count(*) FROM applications WHERE screenshot='' ").fetchone()[0] == 5
        assert unrelated.exists()
        assert not stale.exists()
        assert other_session.exists()
    finally:
        con.close()
