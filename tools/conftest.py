"""Tests run against autopilot/config.example.json when the user has no config.json yet."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot as A  # noqa: E402

if not A.CFG.exists():
    A.CFG = A.ROOT / "autopilot" / "config.example.json"
