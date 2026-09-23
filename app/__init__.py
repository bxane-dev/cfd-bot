"""CFD bot application package.

The project historically used top-level imports. Keep the app directory on
sys.path so those imports remain stable after the repository reorganization.

Author: bxane
"""
from pathlib import Path
import sys

__author__ = "bxane"

_APP_DIR = str(Path(__file__).resolve().parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
