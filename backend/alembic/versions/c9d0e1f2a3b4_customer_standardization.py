"""Customer standardization: mapping registry, history, sales_records tracking.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-06

Additive only:
- creates customer_alias_mappings and customer_mapping_history (both empty);
- adds three NULLABLE columns to sales_records. Existing sales rows are not
  updated: NULL means "imported before customer standardization existed".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None

_MAP = "customer_alias_mappings"
_HIST = "customer_mapping_history"


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if _MAP not in tables:
        op.create_table(
            _MAP,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column("original_customer_name", sa.String(255), nullable=False),
            sa.Column("normalized_original_name", sa.String(255), nullable=False),
            sa.Column("standardized_customer_name", sa.String(255), nullable=False),
            sa.Column("scope", sa.String(20), nullable=False, server_default="GLOBAL"),
            sa.Column("source", sa.String(100), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="APPROVED"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_new_customer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("approved_by_name", sa.String(255), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "uq_customer_alias_mappings_dist_key_active",
            _MAP,
            ["distributor_id", "normalized_original_name"],
            unique=True,
            postgresql_where=sa.text("is_active = true AND distributor_id IS NOT NULL"),
        )
        op.create_index(
            "uq_customer_alias_mappings_global_key_active",
            _MAP,
            ["normalized_original_name"],
            unique=True,
            postgresql_where=sa.text("is_active = true AND distributor_id IS NULL"),
        )
        op.create_index("ix_customer_alias_mappings_distributor_id", _MAP, ["distributor_id"])
        op.create_index("ix_customer_alias_mappings_standardized", _MAP, ["standardized_customer_name"])
        op.create_index("ix_customer_alias_mappings_created_at", _MAP, ["created_at"])

    if _HIST not in tables:
        op.create_table(
            _HIST,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("mapping_id", sa.Integer(), sa.ForeignKey(f"{_MAP}.id", ondelete="SET NULL"), nullable=True),
            sa.Column("original_customer_name", sa.String(255), nullable=False),
            sa.Column("normalized_original_name", sa.String(255), nullable=False),
            sa.Column("previous_value", sa.String(255), nullable=True),
            sa.Column("new_value", sa.String(255), nullable=True),
            sa.Column("scope", sa.String(20), nullable=False),
            sa.Column(
                "distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("changed_by_name", sa.String(255), nullable=True),
            sa.Column("email_id", sa.Integer(), nullable=True),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_customer_mapping_history_mapping_id", _HIST, ["mapping_id"])
        op.create_index("ix_customer_mapping_history_normalized", _HIST, ["normalized_original_name"])
        op.create_index("ix_customer_mapping_history_distributor_id", _HIST, ["distributor_id"])

    sales_cols = {c["name"] for c in insp.get_columns("sales_records")}
    # Nullable, no server default: metadata-only change, existing rows untouched.
    if "original_customer_name" not in sales_cols:
        op.add_column("sales_records", sa.Column("original_customer_name", sa.String(255), nullable=True))
    if "customer_standardization_status" not in sales_cols:
        op.add_column("sales_records", sa.Column("customer_standardization_status", sa.String(20), nullable=True))
    if "customer_mapping_id" not in sales_cols:
        op.add_column("sales_records", sa.Column("customer_mapping_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_records", "customer_mapping_id")
    op.drop_column("sales_records", "customer_standardization_status")
    op.drop_column("sales_records", "original_customer_name")

    op.drop_index("ix_customer_mapping_history_distributor_id", table_name=_HIST)
    op.drop_index("ix_customer_mapping_history_normalized", table_name=_HIST)
    op.drop_index("ix_customer_mapping_history_mapping_id", table_name=_HIST)
    op.drop_table(_HIST)

    op.drop_index("ix_customer_alias_mappings_created_at", table_name=_MAP)
    op.drop_index("ix_customer_alias_mappings_standardized", table_name=_MAP)
    op.drop_index("ix_customer_alias_mappings_distributor_id", table_name=_MAP)
    op.drop_index("uq_customer_alias_mappings_global_key_active", table_name=_MAP)
    op.drop_index("uq_customer_alias_mappings_dist_key_active", table_name=_MAP)
    op.drop_table(_MAP)
