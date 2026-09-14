"""Two columns: the upstream handle, and the model a task ran on.

``upstream_task_id`` is what made the primary key free to become the caller's
``request_id`` — the upstream's own id moved out of the key and into a column, so a row
can exist *before* the POST that would have produced that id. Purely internal; it never
reaches a caller. NULL on every pre-existing row, whose ``id`` is the upstream id, which
is why ``TaskManager.poll`` reads ``upstream_task_id or id`` and why nothing is
backfilled.

``model_used`` is the public model name, denormalized from ``adapter_id``. It buys
nothing at runtime and exists for reporting: with the row now written before the POST,
``updated_at - created_at`` over terminal rows is a real end-to-end latency, and this
column is what lets that be grouped per model in plain SQL.

Both additions are guarded, for the same reason as the baseline: a store that was
started once by the pre-Alembic code path already has them.

Revision ID: 0002_task_recovery_columns
Revises: 0001_initial_tasks
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_task_recovery_columns"
down_revision = "0001_initial_tasks"
branch_labels = None
depends_on = None

_INDEX = "idx_tasks_upstream"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("tasks")}
    if "upstream_task_id" not in existing:
        op.add_column("tasks", sa.Column("upstream_task_id", sa.Text(), nullable=True))
    if "model_used" not in existing:
        op.add_column("tasks", sa.Column("model_used", sa.Text(), nullable=True))
    if _INDEX not in {i["name"] for i in sa.inspect(bind).get_indexes("tasks")}:
        op.create_index(_INDEX, "tasks", ["upstream_task_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="tasks")
    op.drop_column("tasks", "model_used")
    op.drop_column("tasks", "upstream_task_id")
