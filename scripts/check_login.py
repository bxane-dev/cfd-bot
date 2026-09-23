from pathlib import Path
import re
import sys

env = Path(__file__).resolve().parents[1] / ".env"
if not env.exists():
    print("Missing .env — run fill_capital.bat first")
    sys.exit(1)

text = env.read_text(encoding="utf-8", errors="ignore")
missing = []
for key in ("CAPITAL_EMAIL", "CAPITAL_API_KEY", "CAPITAL_API_PASSWORD"):
    m = re.search(rf"^{key}=(.*)$", text, re.M)
    if not m or not m.group(1).strip():
        missing.append(key)
if missing:
    print("Missing:", ", ".join(missing))
    sys.exit(1)
print("Capital.com credentials are filled.")
print("CAPITAL_ACCOUNT_ID is optional; leave it blank unless you specifically want to switch accounts.")
