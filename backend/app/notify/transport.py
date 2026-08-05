"""Delivery transports — the ONLY code that talks to the outside world.

`WhatsAppTransport` is a deliberate seam: today's implementation posts to
skubot's unofficial bridge (one WhatsApp presence for the org); when the
official Cloud API account lands, it becomes a second implementation behind
the same two methods. Messages stay plain single-string texts so they can
survive the official API's template-message rules.

Bridge HTTP contract (implemented by skubot):
    POST {bridge_url}/send   {"to": "+1615…", "text": "…"}   Bearer token
        → 200 {"ok": true}                    anything else = failure
    GET  {bridge_url}/status                                  Bearer token
        → 200 {"connected": true|false, "detail": "…"}
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from ..config import Settings


class TransportError(Exception):
    """A live delivery attempt failed (network, bridge down, refusal…)."""


@dataclass
class TransportHealth:
    connected: bool
    detail: str = ""


class WhatsAppTransport(Protocol):
    """What any WhatsApp backend must provide. Keep this surface tiny —
    it's the migration path to the official Cloud API."""

    name: str

    def send(self, to_phone: str, text: str) -> None:
        """Deliver one text. Raises TransportError on failure."""
        ...

    def check_health(self) -> TransportHealth:
        """Cheap liveness probe (never raises — report health honestly)."""
        ...


class BridgeWhatsAppTransport:
    """skubot's unofficial bridge. Sessions drop and bans happen — treat
    every failure as ordinary (the service falls back to email) and let the
    worker's probe keep the admin status page honest."""

    name = "whatsapp-bridge"

    def __init__(self, settings: Settings):
        self._base = settings.whatsapp_bridge_url.rstrip("/")
        self._token = settings.whatsapp_bridge_token.get_secret_value()
        self._timeout = settings.whatsapp_bridge_timeout_seconds

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def send(self, to_phone: str, text: str) -> None:
        try:
            resp = httpx.post(
                f"{self._base}/send",
                json={"to": to_phone, "text": text},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise TransportError(f"bridge unreachable: {e}") from e
        if resp.status_code != 200:
            raise TransportError(f"bridge returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            ok = bool(resp.json().get("ok"))
        except ValueError:
            ok = False
        if not ok:
            raise TransportError(f"bridge did not accept the message: {resp.text[:200]}")

    def check_health(self) -> TransportHealth:
        try:
            resp = httpx.get(
                f"{self._base}/status", headers=self._headers(), timeout=self._timeout
            )
            if resp.status_code != 200:
                return TransportHealth(False, f"HTTP {resp.status_code} from bridge /status")
            data = resp.json()
            return TransportHealth(
                connected=bool(data.get("connected")),
                detail=str(data.get("detail") or data.get("state") or ""),
            )
        except (httpx.HTTPError, ValueError) as e:
            return TransportHealth(False, f"bridge unreachable: {e}")


class SmtpEmailTransport:
    """The fallback channel. Also deliberately tiny."""

    name = "smtp"

    def __init__(self, settings: Settings):
        self._settings = settings

    def send(self, to_email: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)
        self._deliver(msg)

    def send_with_attachments(
        self,
        to_emails: list[str],
        subject: str,
        text: str,
        attachments: list[tuple[str, bytes, str]],  # (filename, data, content_type)
        cc_emails: list[str] | None = None,
    ) -> str:
        """Multipart send for order emails (CSV/XLSX purchase orders). Returns
        the generated Message-ID so replies can be threaded back."""
        from email.utils import make_msgid

        msg = EmailMessage()
        msg["To"] = ", ".join(to_emails)
        if cc_emails:
            msg["Cc"] = ", ".join(cc_emails)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(text)
        for filename, data, content_type in attachments:
            maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
            msg.add_attachment(
                data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename
            )
        self._deliver(msg)
        return str(msg["Message-ID"])

    def _deliver(self, msg: EmailMessage) -> None:
        s = self._settings
        msg["From"] = s.smtp_from
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_starttls:
                    # The default context verifies the certificate chain AND the
                    # hostname. Bare starttls() does neither, so anyone able to
                    # answer for the SMTP host gets the password on the next line.
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise TransportError(f"smtp send failed: {e}") from e
