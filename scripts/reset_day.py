#!/usr/bin/env python3
"""Manual daily-loss reset. Does not close positions."""
from dotenv import load_dotenv

from main import ROOT, load_cfg
from memory import remember
from risk import RiskManager

load_dotenv(ROOT / ".env")
cfg = load_cfg()
snap = RiskManager(cfg).reset_day(why="cli")
remember("risk", "manual daily loss reset", how="reset_day.py", extra=snap)
print("daily loss reset")
for k, v in snap.items():
    print(f"  {k}: {v}")
