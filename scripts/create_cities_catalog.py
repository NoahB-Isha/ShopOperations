"""
Creates a "Cities" catalog from the CONSOLIDATED sheet of
Cities_Assortment_Recommendation_v3.xlsx via the order-lists import API.

Usage:
    cd "Shop Ops App"
    python scripts/create_cities_catalog.py

Token is pre-filled. Re-paste a fresh one if you get a 401.
"""
import urllib.request
import urllib.error
import json
import uuid
import sys
from pathlib import Path

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzOCIsImVwIjoxLCJpYXQiOjE3ODcwODMyNzEsImV4cCI6MTc4OTY3NTI3MSwiaXNzIjoic2hvcC1vcHMifQ.9Efq-KlYPOrxttcgIKo2SmDu8BwxuEgBq6DKFBaTMJI"
BASE = "https://shopops-backend.onrender.com/api/v1"

# The xlsx should be next to this script, or adjust the path.
XLSX = Path(__file__).parent.parent / "docs/reference/Cities_Assortment_Recommendation_v3.xlsx"
if not XLSX.exists():
    # Fall back to Downloads or current dir
    candidates = [
        Path.home() / "Downloads/Cities_Assortment_Recommendation_v3.xlsx",
        Path("Cities_Assortment_Recommendation_v3.xlsx"),
    ]
    for c in candidates:
        if c.exists():
            XLSX = c
            break
    else:
        sys.exit(f"ERROR: can't find the xlsx. Put it at {XLSX} or in ~/Downloads/")

print(f"Using file: {XLSX}")

# ── multipart form-data upload ──────────────────────────────────────────────
boundary = uuid.uuid4().hex
xlsx_bytes = XLSX.read_bytes()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="name"\r\n\r\n'
    f"Cities\r\n"
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{XLSX.name}"\r\n'
    f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
).encode() + xlsx_bytes + f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    f"{BASE}/order-lists/import",
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    },
)

print("Uploading to /order-lists/import ...")
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
except urllib.error.HTTPError as e:
    body_err = e.read().decode()
    sys.exit(f"HTTP {e.code}: {body_err}")

print(json.dumps(result, indent=2))
print(f"\n✓ Catalog id: {result.get('id')} — '{result.get('name')}'")
print(f"  matched={result.get('matched')}  skipped={result.get('skipped')}  unmatched={result.get('unmatched')}")
