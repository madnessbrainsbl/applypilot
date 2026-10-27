"""Run tools/test_*.py self-checks with an isolated example profile and database."""
import os
import shutil
import runpy
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autopilot as A  # noqa: E402
import db  # noqa: E402

def main():
    failed = []
    pytest_mode = "--pytest" in sys.argv
    files = [f for f in sorted(HERE.glob("test_*.py")) if len(sys.argv) < 2 or sys.argv[1] in f.name]
    with tempfile.TemporaryDirectory(prefix="applypilot-tests-") as directory:
        folder = Path(directory)
        example = (A.ROOT / "autopilot/config.example.json").read_bytes()
        A.ROOT = folder
        runtime = folder / "autopilot"
        runtime.mkdir()
        shutil.copytree(HERE.parent / "autopilot/seeds", runtime / "seeds")
        A.SHOTS, A.COVERS = runtime / "screenshots", runtime / "covers"
        A.CONTROL, A.PROGRESS = runtime / "control.json", runtime / "progress.json"
        A.RESUMES = runtime / "resumes"
        temp = folder / "temp"
        temp.mkdir()
        tempfile.tempdir = str(temp)
        for variable in ("TEMP", "TMP", "TMPDIR"):
            os.environ[variable] = str(temp)
        A.CFG = folder / "config.json"
        A.CFG.write_bytes(example)
        A.SECRETS = folder / "secrets.env"
        db.DB_PATH = folder / "jobs.db"
        import importlib
        for name in ("jobwatch", "jsonld_careers", "findall_osint", "remote_visa_boards", "security_osint", "osint_sources", "sponsors", "opencode_llm"):
            module = importlib.import_module(name)
            for key, value in vars(module).copy().items():
                if isinstance(value, Path) and value.is_absolute() and value.is_relative_to(HERE.parent):
                    setattr(module, key, folder / value.relative_to(HERE.parent))
        os.environ["JOBWATCH_EXPORT_DIR"] = str(runtime / "jobwatch")
        os.environ["AUTOPILOT_SECRETS_FILE"] = str(A.SECRETS)
        if pytest_mode:
            os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            import pytest
            raise SystemExit(pytest.main([str(HERE.parent / "tests"), "--basetemp", str(temp / "pytest"), *sys.argv[2:]]))
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


if __name__ == '__main__':
    main()
