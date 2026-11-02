"""FG Master data, mapping registry extension, standardization tracking.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-29

Additive only:
- creates fg_master_versions, fg_master_products, product_mapping_history,
  product_standardization_snapshots (all empty; the master is uploaded per
  environment by a Super Admin, never seeded here);
- adds approval/scope columns to product_alias_mappings and fills them for the
  existing rows of that table only;
- adds three NULLABLE columns to sales_records. Existing sales rows are not
  updated: NULL means "imported before product standardization existed".
"""

from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_ALIAS = "product_alias_mappings"


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "fg_master_versions" not in tables:
        op.create_table(
            "fg_master_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("file_name", sa.String(255), nullable=False),
            sa.Column("file_sha256", sa.String(64), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sheet_name", sa.String(255), nullable=True),
            sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("uploaded_by_name", sa.String(255), nullable=True),
            sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("published_by_name", sa.String(255), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("version_number", name="uq_fg_master_versions_number"),
        )
        op.create_index(
            "uq_fg_master_versions_single_active",
            "fg_master_versions",
            ["status"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )
        op.create_index("ix_fg_master_versions_created_at", "fg_master_versions", ["created_at"])

    if "fg_master_products" not in tables:
        op.create_table(
            "fg_master_products",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "version_id",
                sa.Integer(),
                sa.ForeignKey("fg_master_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("material_description", sa.String(255), nullable=False),
            sa.Column("normalized_description", sa.String(255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            *_timestamps(),
            sa.UniqueConstraint(
                "version_id", "normalized_description", name="uq_fg_master_products_version_normalized"
            ),
        )
        op.create_index("ix_fg_master_products_version_id", "fg_master_products", ["version_id"])
        op.create_index("ix_fg_master_products_normalized", "fg_master_products", ["normalized_description"])
        op.create_index("ix_fg_master_products_created_at", "fg_master_products", ["created_at"])

    alias_cols = {c["name"] for c in insp.get_columns(_ALIAS)} if _ALIAS in tables else set()
    if _ALIAS in tables:
        additions = [
            sa.Column("normalized_original_name", sa.String(255), nullable=True),
            sa.Column("scope", sa.String(20), nullable=False, server_default="GLOBAL"),
            sa.Column("status", sa.String(20), nullable=False, server_default="APPROVED"),
            sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("approved_by_name", sa.String(255), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("master_version_id", sa.Integer(), nullable=True),
            sa.Column("is_custom_value", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        ]
        for col in additions:
            if col.name not in alias_cols:
                op.add_column(_ALIAS, col)
        existing_idx = {i["name"] for i in insp.get_indexes(_ALIAS)}
        if "ix_product_alias_mappings_original_key" not in existing_idx:
            op.create_index(
                "ix_product_alias_mappings_original_key", _ALIAS, ["normalized_original_name"]
            )
        # Existing (pre-feature) mappings are Super Admin curated: mark them approved,
        # set their scope from distributor_id and compute the matching key.
        rows = conn.execute(
            sa.text(
                f"SELECT id, original_product_name, distributor_id, created_by, created_at "
                f"FROM {_ALIAS} WHERE normalized_original_name IS NULL"
            )
        ).fetchall()
        for row in rows:
            conn.execute(
                sa.text(
                    f"UPDATE {_ALIAS} SET normalized_original_name = :k, "
                    f"scope = :scope, status = 'APPROVED', "
                    f"approved_by = COALESCE(approved_by, :uid), "
                    f"approved_at = COALESCE(approved_at, :ts) WHERE id = :id"
                ),
                {
                    "k": _key(row.original_product_name)[:255],
                    "scope": "DISTRIBUTOR" if row.distributor_id is not None else "GLOBAL",
                    "uid": row.created_by,
                    "ts": row.created_at,
                    "id": row.id,
                },
            )

    if "product_mapping_history" not in tables:
        op.create_table(
            "product_mapping_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "mapping_id",
                sa.Integer(),
                sa.ForeignKey("product_alias_mappings.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("original_product_name", sa.String(255), nullable=False),
            sa.Column("normalized_original_name", sa.String(255), nullable=False),
            sa.Column("previous_value", sa.String(255), nullable=True),
            sa.Column("new_value", sa.String(255), nullable=True),
            sa.Column("scope", sa.String(20), nullable=False),
            sa.Column("distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("changed_by_name", sa.String(255), nullable=True),
            sa.Column("email_id", sa.Integer(), nullable=True),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("report_id", sa.Integer(), nullable=True),
            sa.Column("master_version_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_product_mapping_history_mapping_id", "product_mapping_history", ["mapping_id"])
        op.create_index(
            "ix_product_mapping_history_normalized", "product_mapping_history", ["normalized_original_name"]
        )
        op.create_index("ix_product_mapping_history_distributor_id", "product_mapping_history", ["distributor_id"])

    if "product_standardization_snapshots" not in tables:
        op.create_table(
            "product_standardization_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "email_id",
                sa.Integer(),
                sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "attachment_id",
                sa.Integer(),
                sa.ForeignKey("email_attachments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attachment_name", sa.String(500), nullable=False),
            sa.Column("content_hash", sa.String(128), nullable=True),
            sa.Column("distributor_id", sa.Integer(), nullable=True),
            sa.Column("parser_used", sa.String(100), nullable=True),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("columns", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("rows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            *_timestamps(),
            sa.UniqueConstraint("email_id", "attachment_id", name="uq_product_std_snapshots_email_attachment"),
        )
        op.create_index("ix_product_std_snapshots_email_id", "product_standardization_snapshots", ["email_id"])
        op.create_index(
            "ix_product_standardization_snapshots_created_at",
            "product_standardization_snapshots",
            ["created_at"],
        )

    sales_cols = {c["name"] for c in insp.get_columns("sales_records")}
    # Nullable, no server default: adding these is a metadata-only change and
    # leaves every existing sales row untouched.
    if "original_product_name" not in sales_cols:
        op.add_column("sales_records", sa.Column("original_product_name", sa.String(255), nullable=True))
    if "product_standardization_status" not in sales_cols:
        op.add_column("sales_records", sa.Column("product_standardization_status", sa.String(20), nullable=True))
    if "product_mapping_id" not in sales_cols:
        op.add_column("sales_records", sa.Column("product_mapping_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_records", "product_mapping_id")
    op.drop_column("sales_records", "product_standardization_status")
    op.drop_column("sales_records", "original_product_name")

    op.drop_index("ix_product_standardization_snapshots_created_at", table_name="product_standardization_snapshots")
    op.drop_index("ix_product_std_snapshots_email_id", table_name="product_standardization_snapshots")
    op.drop_table("product_standardization_snapshots")

    op.drop_index("ix_product_mapping_history_distributor_id", table_name="product_mapping_history")
    op.drop_index("ix_product_mapping_history_normalized", table_name="product_mapping_history")
    op.drop_index("ix_product_mapping_history_mapping_id", table_name="product_mapping_history")
    op.drop_table("product_mapping_history")

    op.drop_index("ix_product_alias_mappings_original_key", table_name=_ALIAS)
    for col in (
        "is_custom_value",
        "master_version_id",
        "confidence",
        "approved_at",
        "approved_by_name",
        "approved_by",
        "status",
        "scope",
        "normalized_original_name",
    ):
        op.drop_column(_ALIAS, col)

    op.drop_index("ix_fg_master_products_created_at", table_name="fg_master_products")
    op.drop_index("ix_fg_master_products_normalized", table_name="fg_master_products")
    op.drop_index("ix_fg_master_products_version_id", table_name="fg_master_products")
    op.drop_table("fg_master_products")
    op.drop_index("ix_fg_master_versions_created_at", table_name="fg_master_versions")
    op.drop_index("uq_fg_master_versions_single_active", table_name="fg_master_versions")
    op.drop_table("fg_master_versions")
