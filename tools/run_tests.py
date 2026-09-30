"""Run tools/test_*.py self-checks with an isolated example profile and database."""
import runpy
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autopilot as A  # noqa: E402
import db  # noqa: E402

failed = []
files = [f for f in sorted(HERE.glob("test_*.py")) if len(sys.argv) < 2 or sys.argv[1] in f.name]
with tempfile.TemporaryDirectory(prefix="autopilot-tests-") as directory:
    folder = Path(directory)
    A.CFG = folder / "config.json"
    A.CFG.write_bytes((A.ROOT / "autopilot/config.example.json").read_bytes())
    A.SECRETS = folder / "secrets.env"
    db.DB_PATH = folder / "jobs.db"
    for f in files:
        try:
            runpy.run_path(str(f), run_name="__main__")
        except BaseException as e:  # SystemExit(0) from a test's own main() is a pass
            if isinstance(e, SystemExit) and not e.code:
                continue
            failed.append(f.name)
            print(f"FAIL {f.name}: {e!r}", file=sys.stderr)
            traceback.print_exc(limit=3)
print(f"\n{len(files) - len(failed)}/{len(files)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
sys.exit(1 if failed or not files else 0)
