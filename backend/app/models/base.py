from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names so Alembic migrations stay deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on Postgres, plain JSON elsewhere (unit tests run on SQLite).
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def elapsed_since(stamp: datetime | None, now: datetime | None = None) -> float:
    """Seconds since `stamp`, or `inf` when there is no stamp yet.

    Every poller in the app throttles on a "when did we last look" column, and
    every one of them had to remember that SQLite hands those back NAIVE while
    Postgres hands them back aware — comparing the two raises TypeError, which
    is a runtime error in a background poll rather than a test failure. Four
    copies of that dance is four chances to forget; this is the one copy.

    `inf` for a missing stamp is the useful default: "never looked" always
    means "due".
    """
    if stamp is None:
        return float("inf")
    now = now or utcnow()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now.tzinfo)
    return (now - stamp).total_seconds()


def is_due(stamp: datetime | None, every_seconds: float, now: datetime | None = None) -> bool:
    """True when `every_seconds` have passed since `stamp` (or it is unset)."""
    return elapsed_since(stamp, now) >= every_seconds


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
