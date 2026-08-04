"""Export the API's OpenAPI schema to docs/api/openapi.json.

The schema is generated straight from the FastAPI app, so it is always in
lockstep with the code — rerun after any router change (`make openapi`) and
commit the result. The committed copy exists so integrators can grab the
machine-readable contract without running the backend; the live server also
serves it at /api/openapi.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> None:
    out = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"
    )
    schema = create_app().openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {out} — {len(schema.get('paths', {}))} paths, OpenAPI {schema.get('openapi')}")


if __name__ == "__main__":
    main()
