"""Original worksheet rows on the standardization snapshot.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-13

Additive only: one NULLABLE JSONB column on product_standardization_snapshots.
No existing rows are updated; old snapshots are rebuilt on their next open.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

_TABLE = "product_standardization_snapshots"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if "source_table" not in cols:
        op.add_column(_TABLE, sa.Column("source_table", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "source_table")
