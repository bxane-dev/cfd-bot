from pathlib import Path
import os
import sys

from dotenv import load_dotenv

env = Path(__file__).resolve().parents[1] / ".env"
if env.exists():
    load_dotenv(env)

identifier = (os.getenv("CAPITAL_EMAIL") or os.getenv("CAPITAL_IDENTIFIER") or "").strip()
api_key = (os.getenv("CAPITAL_API_KEY") or "").strip()
password = (os.getenv("CAPITAL_API_PASSWORD") or "").strip()

missing = []
if not identifier:
    missing.append("CAPITAL_EMAIL")
if not api_key:
    missing.append("CAPITAL_API_KEY")
if not password:
    missing.append("CAPITAL_API_PASSWORD")

if missing:
    if not env.exists():
        print("Missing .env — copy .env.example to .env and fill in your Capital.com credentials")
    print("Missing:", ", ".join(missing))
    sys.exit(1)

print("Capital.com credentials are filled.")
print("CAPITAL_ACCOUNT_ID is optional; leave it blank unless you specifically want to switch accounts.")
