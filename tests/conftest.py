import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
TOOLS_DIR = PROJECT_DIR / "tools"

for p in [str(PROJECT_DIR), str(TOOLS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)
