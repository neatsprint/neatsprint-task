"""enable tenant row-level security

Revision ID: a1b2c3d4e5f6
Revises: 888c3202cf97
"""

from __future__ import annotations

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "888c3202cf97"
branch_labels = None
depends_on = None

TABLES = (
    "projects",
    "project_teams",
    "backlog_items",
    "sprints",
    "sprint_items",
    "item_comments",
    "item_history",
)

POLICY = (
    "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({POLICY}) WITH CHECK ({POLICY})"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
