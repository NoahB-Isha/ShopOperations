"""Order-mailbox ingestion — a polite, READ-ONLY IMAP poll the worker runs.

Scope discipline (project brief): mailbox access is read-only and scoped to
order threads. The poller never marks, moves, or deletes anything — it tracks
the last-processed UID in the `ordering_mailbox_state` AppSetting and only
ingests messages it can match to a purchase order (References/In-Reply-To
against our sent Message-IDs, or the ILAPP-PO-… reference token). Everything
else in the inbox is ignored and stays untouched.

Not configured (blank IMAP_HOST) → the poller is a no-op; replies can always
be ingested by hand via the admin "paste a reply" endpoint.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
from datetime import datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import utcnow
from .service import get_app_setting, set_app_setting
from .tracking import find_order_for_inbound, ingest_email

log = logging.getLogger("ordering.mailbox")

MAILBOX_STATE_KEY = "ordering_mailbox_state"
MAX_BODY_CHARS = 100_000
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def mailbox_configured(settings: Settings) -> bool:
    return bool(settings.imap_host and settings.imap_username)


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if part.get_content_type() == "text/html":
        import re

        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
    return text[:MAX_BODY_CHARS]


def _attachments(msg: EmailMessage) -> list[tuple[str, bytes, str]]:
    out = []
    for part in msg.iter_attachments():
        filename = part.get_filename() or "attachment"
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        if len(payload) > MAX_ATTACHMENT_BYTES:
            log.info("skipping oversized attachment %s (%d bytes)", filename, len(payload))
            continue
        out.append((filename, payload, part.get_content_type()))
    return out


def _received_at(msg: EmailMessage) -> datetime | None:
    try:
        raw = msg.get("Date")
        return parsedate_to_datetime(raw) if raw else None
    except (TypeError, ValueError):
        return None


def poll_mailbox(db: Session, settings: Settings) -> int:
    """One poll pass: ingest new, order-matching messages. Returns how many
    messages were ingested. Raises nothing upward — the worker loop logs."""
    if not mailbox_configured(settings):
        return 0
    state = get_app_setting(db, MAILBOX_STATE_KEY)
    last_uid = int(state.get("last_uid") or 0)

    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.imap_username, settings.imap_password.get_secret_value())
        imap.select(settings.imap_folder, readonly=True)  # READ-ONLY, always
        status, data = imap.uid("search", f"UID {last_uid + 1}:*")
        if status != "OK":
            log.warning("mailbox search failed: %s", status)
            return 0
        uids = [int(u) for u in (data[0].split() if data and data[0] else [])]
        uids = [u for u in uids if u > last_uid]
        ingested = 0
        max_seen = last_uid
        for uid in sorted(uids)[:50]:  # polite: bounded batch per pass
            max_seen = max(max_seen, uid)
            status, fetched = imap.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            msg = email.message_from_bytes(fetched[0][1], policy=email.policy.default)
            subject = str(msg.get("Subject") or "")
            body = _body_text(msg)  # type: ignore[arg-type]
            refs = " ".join(
                str(msg.get(h) or "") for h in ("In-Reply-To", "References")
            ).strip()
            order = find_order_for_inbound(db, subject, body, refs)
            if order is None:
                continue  # not an order thread — leave it alone
            ingest_email(
                db,
                settings,
                order,
                sender=str(msg.get("From") or ""),
                subject=subject,
                body=body,
                rfc_message_id=str(msg.get("Message-ID") or ""),
                occurred_at=_received_at(msg),  # type: ignore[arg-type]
                attachments=_attachments(msg),  # type: ignore[arg-type]
            )
            ingested += 1
        if max_seen > last_uid:
            set_app_setting(
                db,
                MAILBOX_STATE_KEY,
                {**state, "last_uid": max_seen, "last_poll_at": utcnow().isoformat()},
            )
        db.commit()
        return ingested
