"""initial schema: users, api_keys, models, user_model_config, tickets

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", _TS),
        sa.Column("revoked_at", _TS),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "models",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("provider_model", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(500)),
        sa.Column("api_key_enc", sa.Text()),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "provider IN ('openai', 'anthropic', 'google', 'byom', 'mock')",
            name="ck_models_provider",
        ),
        sa.CheckConstraint("tier IN ('economy', 'standard', 'premium')", name="ck_models_tier"),
    )
    op.create_index("ix_models_owner_user_id", "models", ["owner_user_id"])
    op.create_table(
        "user_model_config",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "default_model_id", sa.String(64), sa.ForeignKey("models.id", ondelete="SET NULL")
        ),
        sa.Column(
            "fallback_model_id", sa.String(64), sa.ForeignKey("models.id", ondelete="SET NULL")
        ),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("request_id", sa.String(128)),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')", name="ck_tickets_priority"
        ),
    )
    op.create_index("ix_tickets_user_id", "tickets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_user_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("user_model_config")
    op.drop_index("ix_models_owner_user_id", table_name="models")
    op.drop_table("models")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("users")
