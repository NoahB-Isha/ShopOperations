"""
Creates a catalog from a CSV or XLSX file via POST /order-lists/import.

Usage:
    python scripts/create_cities_catalog.py <file> <"Catalog Name">

Examples:
    python scripts/create_cities_catalog.py "docs/reference/Cities Ordering List.csv" "Cities"
    python scripts/create_cities_catalog.py "docs/reference/iii_departments_order_list.csv" "III Departments"

Token: update TOKEN below if you get a 401 (grab from DevTools → Network → any
/api/v1/ request → Authorization header).
"""
import sys
import uuid
import urllib.request
import urllib.error
import json
from pathlib import Path

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzOCIsImVwIjoxLCJpYXQiOjE3ODcwODMyNzEsImV4cCI6MTc4OTY3NTI3MSwiaXNzIjoic2hvcC1vcHMifQ.9Efq-KlYPOrxttcgIKo2SmDu8BwxuEgBq6DKFBaTMJI"
BASE = "https://shopops-backend.onrender.com/api/v1"

# ── args ──────────────────────────────────────────────────────────────────────
if len(sys.argv) != 3:
    sys.exit(__doc__)

file_path = Path(sys.argv[1])
catalog_name = sys.argv[2]

if not file_path.exists():
    sys.exit(f"ERROR: file not found: {file_path}")

ext = file_path.suffix.lower()
if ext == ".csv":
    content_type = "text/csv"
elif ext in (".xlsx", ".xls"):
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
else:
    sys.exit(f"ERROR: unsupported file type: {ext} (use .csv or .xlsx)")

print(f"File:    {file_path} ({file_path.stat().st_size // 1024} KB)")
print(f"Catalog: {catalog_name!r}")
print(f"API:     {BASE}/order-lists/import")
print()

# ── multipart upload ──────────────────────────────────────────────────────────
boundary = uuid.uuid4().hex
file_bytes = file_path.read_bytes()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="name"\r\n\r\n'
    f"{catalog_name}\r\n"
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
    f"Content-Type: {content_type}\r\n\r\n"
).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    f"{BASE}/order-lists/import",
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    },
)

print("Uploading...")
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
except urllib.error.HTTPError as e:
    sys.exit(f"HTTP {e.code}: {e.read().decode()}")

print(json.dumps(result, indent=2))
print()
print(f"✓  id={result.get('id')}  name={result.get('name')!r}")
print(f"   matched={result.get('matched')}  skipped={result.get('skipped')}  unmatched={result.get('unmatched')}")
if result.get("unmatched_items"):
    print("\nUnmatched rows (not in the product catalog):")
    for item in result["unmatched_items"][:20]:
        print(f"  {item}")
    if len(result.get("unmatched_items", [])) > 20:
        print(f"  … and {len(result['unmatched_items']) - 20} more")
