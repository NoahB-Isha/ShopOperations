"""Background worker: Odoo snapshot syncs on their cadence, plus the
notification pump (phase 3).

Polite-client policy (from the brief): stock & incoming a few times a day,
products twice a day, sales hourly (current-month incrementals; the previous
month is folded in once a day; the 24-month backfill happens automatically on
the very first sales run). Later phases add email ingestion here.

Notifications: the API delivers best-effort inline; this loop sweeps up the
stragglers (retries with backoff until the attempt cap) and probes the
WhatsApp bridge so the admin status page shows honest channel health.

The loop wakes every minute, runs whatever is due (serially — one polite
client), and staggers domains so they don't all fire at once. Every run is
recorded in sync_runs/sync_state; failures never clobber the last good
snapshot and auth failures flip the loud flag the status page watches.
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import SYNC_DOMAINS
from app.notify.service import deliver_pending, email_channel_snapshot, probe_whatsapp_bridge
from app.sync.runner import get_or_create_state, run_domain

log = logging.getLogger("worker")

# initial stagger (seconds) so domains don't stampede on first boot
STAGGER = {"products": 0, "stock": 20, "sales": 40, "incoming": 60}

NOTIFY_SWEEP_SECONDS = 30  # outbox retries
BRIDGE_PROBE_SECONDS = 60  # WhatsApp bridge health → admin status page


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.sessions = get_sessionmaker()
        self.stop = False
        self.booted_at = time.monotonic()
        self._last_notify_sweep = 0.0
        self._last_bridge_probe = 0.0

    def _due(self, domain: str) -> bool:
        if time.monotonic() - self.booted_at < STAGGER[domain]:
            return False
        db = self.sessions()
        try:
            state = get_or_create_state(db, domain)
            if state.last_attempt_at is None:
                return True
            interval = self.settings.sync_interval_minutes(domain) * 60
            last = state.last_attempt_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            return (datetime.now(UTC) - last).total_seconds() >= interval
        finally:
            db.close()

    def _run(self, domain: str) -> None:
        db = self.sessions()
        try:
            run = run_domain(db, self.settings, domain, trigger="scheduled")
            if run and run.status != "success":
                log.warning("%s sync failed: %s", domain, run.error)
        except Exception:  # noqa: BLE001 — the loop must survive anything
            log.exception("unexpected error in %s sync", domain)
        finally:
            db.close()

    def _pump_notifications(self) -> None:
        now = time.monotonic()
        if now - self._last_notify_sweep < NOTIFY_SWEEP_SECONDS:
            return
        self._last_notify_sweep = now
        db = self.sessions()
        try:
            done = deliver_pending(db, self.settings)
            if done:
                log.info("notification sweep: %s reached a terminal state", done)
        except Exception:  # noqa: BLE001 — the loop must survive anything
            log.exception("notification sweep failed")
        finally:
            db.close()

    def _probe_bridge(self) -> None:
        now = time.monotonic()
        if now - self._last_bridge_probe < BRIDGE_PROBE_SECONDS:
            return
        self._last_bridge_probe = now
        db = self.sessions()
        try:
            probe_whatsapp_bridge(db, self.settings)
            email_channel_snapshot(db, self.settings)
        except Exception:  # noqa: BLE001
            log.exception("bridge probe failed")
        finally:
            db.close()

    def run_forever(self) -> None:
        log.info(
            "worker up — odoo mode: %s, cadence (min): %s",
            self.settings.odoo_mode,
            {d: self.settings.sync_interval_minutes(d) for d in SYNC_DOMAINS},
        )
        while not self.stop:
            for domain in SYNC_DOMAINS:
                if self.stop:
                    break
                if self._due(domain):
                    self._run(domain)
            self._pump_notifications()
            self._probe_bridge()
            for _ in range(15):
                if self.stop:
                    break
                time.sleep(1)
        log.info("worker stopped cleanly")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = Worker()

    def _stop(_sig, _frame):
        worker.stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
