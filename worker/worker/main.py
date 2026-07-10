"""Background worker: runs the Odoo snapshot syncs on their cadence.

Polite-client policy (from the brief): stock & incoming a few times a day,
products twice a day, sales hourly (current-month incrementals; the previous
month is folded in once a day; the 24-month backfill happens automatically on
the very first sales run). Later phases add email ingestion and WhatsApp
notification queues here.

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
from app.sync.runner import get_or_create_state, run_domain

log = logging.getLogger("worker")

# initial stagger (seconds) so domains don't stampede on first boot
STAGGER = {"products": 0, "stock": 20, "sales": 40, "incoming": 60}


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.sessions = get_sessionmaker()
        self.stop = False
        self.booted_at = time.monotonic()

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
            for _ in range(60):
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
