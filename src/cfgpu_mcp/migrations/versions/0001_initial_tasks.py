"""Baseline: the tasks table as it stood before migrations existed.

Every existing deployment already has this table — it was created by
``CREATE TABLE IF NOT EXISTS`` at connection time. So this revision is written to be a
no-op against such a database rather than to fail on it: that is what lets an existing
store be brought under Alembic by simply running ``upgrade head``, with no stamping step
and no operator judgement about which revision it is "really" at.

Revision ID: 0001_initial_tasks
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_tasks"
down_revision = None
branch_labels = None
depends_on = None

_INDEX = "idx_tasks_status_created"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("tasks"):
        op.create_table(
            "tasks",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("adapter_id", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("result", sa.Text()),
            sa.Column("error", sa.Text()),
            # sa.Float maps to REAL on SQLite and DOUBLE PRECISION on Postgres —
            # exactly the two types the hand-written DDL used.
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
    # Checked separately: the index existed only on Postgres before this, so a legacy
    # SQLite file has the table but not the index.
    if _INDEX not in {i["name"] for i in sa.inspect(bind).get_indexes("tasks")}:
        op.create_index(_INDEX, "tasks", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="tasks")
    op.drop_table("tasks")
