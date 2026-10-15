"""notifications: alerts received from Alertmanager

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("alertname", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="none"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("starts_at", _TS, nullable=False),
        sa.Column("ends_at", _TS),
        sa.Column("received_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", _TS),
        sa.CheckConstraint("status IN ('firing', 'resolved')", name="ck_notifications_status"),
        sa.UniqueConstraint("fingerprint", "starts_at", name="uq_notifications_alert"),
    )
    op.create_index("ix_notifications_received_at", "notifications", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_received_at", table_name="notifications")
    op.drop_table("notifications")
