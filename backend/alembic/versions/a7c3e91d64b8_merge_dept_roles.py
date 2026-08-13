"""fold the departments roles into the reviewer/requester roles

The app went from eight user types to six (2026-08-13). A departments
reviewer is now an ordinary Order Reviewer whose review zone is "III
Departments", and a departments requester an Order Requester whose center is
a department — the zone already drives every behavioural difference, so the
assignments only need their `role` string rewritten. Scope (zone_id /
center_id) is untouched.

Data only: no schema change. The downgrade is deliberately a no-op — once
merged, nothing records which reviewer used to be a liaison, and guessing
from the zone would resurrect a role the app no longer understands.

Revision ID: a7c3e91d64b8
Revises: e2b6c48a7d15
"""
from alembic import op
from sqlalchemy import text

revision = "a7c3e91d64b8"
down_revision = "e2b6c48a7d15"
branch_labels = None
depends_on = None

MERGES = (("dept_liaison", "zone_coordinator"), ("dept_orderer", "center_orderer"))

# Someone holding BOTH roles at the same scope would collide with
# uq_role_scope(user_id, role, zone_id, center_id); drop the old row rather
# than fail the migration. Written without IS NOT DISTINCT FROM so it runs on
# SQLite (the test database) as well as Postgres.
DROP_DUPES = text(
    """
    DELETE FROM role_assignments
     WHERE id IN (
        SELECT old_row.id
          FROM role_assignments old_row
          JOIN role_assignments kept
            ON kept.user_id = old_row.user_id
           AND kept.role = :new
           AND (kept.zone_id = old_row.zone_id
                OR (kept.zone_id IS NULL AND old_row.zone_id IS NULL))
           AND (kept.center_id = old_row.center_id
                OR (kept.center_id IS NULL AND old_row.center_id IS NULL))
         WHERE old_row.role = :old
     )
    """
)
RENAME = text("UPDATE role_assignments SET role = :new WHERE role = :old")


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in MERGES:
        conn.execute(DROP_DUPES, {"old": old, "new": new})
        conn.execute(RENAME, {"old": old, "new": new})


def downgrade() -> None:
    """No-op: which reviewers were liaisons isn't recorded any more."""
