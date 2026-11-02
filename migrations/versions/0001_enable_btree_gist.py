"""enable btree_gist

Revision ID: 0001
Revises:

The exclusion constraint that prevents double-booking mixes an equality
check on court_id with an overlap check on a time range. Postgres needs the
btree_gist extension to index that combination.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
