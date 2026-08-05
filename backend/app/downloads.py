"""Safe file-download responses.

Both halves of a download header are attacker-influenced here: attachment names
arrive from inbound email (ordering/mailbox.py stores `part.get_filename()`,
which decodes RFC 2231 escapes, so an interior CRLF is reachable with no app
account) and stored content types were echoed straight back. Everything the app
sends as a file goes through this module.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Response

# Types we are willing to echo. Anything else downloads as opaque bytes, so a
# stored "text/html" can never render inside the app's origin.
ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "text/plain",
    "application/pdf",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "image/png",
    "image/jpeg",
}
FALLBACK_CONTENT_TYPE = "application/octet-stream"
MAX_FILENAME_CHARS = 120


def safe_filename(name: str, *, fallback: str = "download") -> str:
    """Single line, no quotes, no path separators. `isprintable()` already drops
    CR/LF/TAB and the Unicode line separators; the explicit set is belt and
    braces for the ones a reader would expect to see named."""
    cleaned = "".join(
        ch for ch in (name or "") if ch.isprintable() and ch not in '"\\/\r\n\t'
    )
    return cleaned.strip().strip(".")[:MAX_FILENAME_CHARS] or fallback


def attachment_headers(filename: str, *, fallback: str = "download") -> dict[str, str]:
    """RFC 6266: a plain-ASCII `filename` for old clients plus percent-encoded
    `filename*` for everything else. Both come from the sanitized value, so no
    control character can reach the header line."""
    name = safe_filename(filename, fallback=fallback)
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'
        )
    }


def download_response(
    data: bytes | str, filename: str, content_type: str | None = None
) -> Response:
    media = (content_type or "").split(";")[0].strip().lower()
    return Response(
        content=data,
        media_type=media if media in ALLOWED_CONTENT_TYPES else FALLBACK_CONTENT_TYPE,
        headers=attachment_headers(filename),
    )
