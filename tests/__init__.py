import sys
from pathlib import Path

_tools_path = str(Path(__file__).resolve().parent.parent / "tools")
if _tools_path not in sys.path:
    sys.path.insert(0, _tools_path)
