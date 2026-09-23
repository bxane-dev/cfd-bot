"""Research tooling package."""
from pathlib import Path
import sys

_APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
