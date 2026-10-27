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
    con.execute("CREATE TABLE applications (screenshot TEXT, submitted INTEGER DEFAULT 0)")
    for i in range(15):
        shot = shots / f"{i}.png"
        shot.write_bytes(b"test")
        os.utime(shot, (i, i))
        con.execute("INSERT INTO applications (screenshot) VALUES (?)", (str(shot.relative_to(tmp_path)),))
    unrelated = temp / "playwright-other-project"
    unrelated.mkdir()
    stale = temp / f"autopilot-cover-{os.getpid()}-test.pdf"
    stale.write_bytes(b"test")
    try:
        result = A.prune_screenshots(con, keep_recent=20)
        assert result["deleted"] == 5
        assert {p.name for p in shots.iterdir()} == {f"{i}.png" for i in range(5, 15)}
        assert con.execute("SELECT count(*) FROM applications WHERE screenshot='' ").fetchone()[0] == 5
        assert unrelated.exists()
        assert not stale.exists()
    finally:
        con.close()


def test_prune_archives_submitted_proofs(tmp_path, monkeypatch):
    shots = tmp_path / "autopilot" / "screenshots"
    shots.mkdir(parents=True)
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "SHOTS", shots)
    monkeypatch.setattr(A.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE applications (screenshot TEXT, submitted INTEGER DEFAULT 0)")
    for i in range(12):
        shot = shots / f"{i}.png"
        shot.write_bytes(b"test")
        os.utime(shot, (i, i))
        # forward-slash paths were never matched by the old relative_to() comparison
        con.execute("INSERT INTO applications VALUES (?, ?)", (f"autopilot/screenshots/{i}.png", int(i == 0)))
    result = A.prune_screenshots(con, keep_recent=10)
    assert result["deleted"] == 1                      # 1.png; 0.png was moved, not deleted
    proof = tmp_path / "autopilot" / "archive" / "proofs" / "0.png"
    assert proof.is_file() and not (shots / "0.png").exists()
    stored = con.execute("SELECT screenshot FROM applications WHERE submitted=1").fetchone()[0]
    assert (tmp_path / stored).is_file(), stored
