"""tickets.id: integer -> uuid

Existing rows get a random UUID (sequential ids cannot be turned into UUIDs
meaningfully). Downgrade renumbers rows 1..N by created_at and restores the
sequence, so the old integer API works again; the original integers are not
recoverable, which is acceptable for local ticket data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # gen_random_uuid() is built into Postgres 13+.
    op.execute("ALTER TABLE tickets ADD COLUMN id_new uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE tickets DROP CONSTRAINT tickets_pkey")
    op.execute("ALTER TABLE tickets DROP COLUMN id")  # drops the owned sequence too
    op.execute("ALTER TABLE tickets RENAME COLUMN id_new TO id")
    op.execute("ALTER TABLE tickets ADD CONSTRAINT tickets_pkey PRIMARY KEY (id)")
    # The app generates ids; the server default only served the backfill.
    op.execute("ALTER TABLE tickets ALTER COLUMN id DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN id_old integer")
    op.execute(
        "UPDATE tickets t SET id_old = r.n FROM "
        "(SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM tickets) r "
        "WHERE t.id = r.id"
    )
    op.execute("ALTER TABLE tickets DROP CONSTRAINT tickets_pkey")
    op.execute("ALTER TABLE tickets DROP COLUMN id")
    op.execute("ALTER TABLE tickets RENAME COLUMN id_old TO id")
    op.execute("CREATE SEQUENCE tickets_id_seq OWNED BY tickets.id")
    op.execute(
        "SELECT setval('tickets_id_seq', COALESCE((SELECT MAX(id) FROM tickets), 0) + 1, false)"
    )
    op.execute("ALTER TABLE tickets ALTER COLUMN id SET DEFAULT nextval('tickets_id_seq')")
    op.execute("ALTER TABLE tickets ALTER COLUMN id SET NOT NULL")
    op.execute("ALTER TABLE tickets ADD CONSTRAINT tickets_pkey PRIMARY KEY (id)")
