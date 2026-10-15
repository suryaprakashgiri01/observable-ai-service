"""ask_jobs: async /ask status and results

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ask_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("model_requested", sa.String(64), nullable=False),
        sa.Column("current_model", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", postgresql.JSONB()),
        sa.Column("request_id", sa.String(128)),
        sa.Column("session_id", sa.String(128)),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", _TS),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'fallback', 'completed', 'failed')",
            name="ck_ask_jobs_status",
        ),
    )
    op.create_index("ix_ask_jobs_user_id", "ask_jobs", ["user_id"])
    op.create_index("ix_ask_jobs_status", "ask_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ask_jobs_status", table_name="ask_jobs")
    op.drop_index("ix_ask_jobs_user_id", table_name="ask_jobs")
    op.drop_table("ask_jobs")
