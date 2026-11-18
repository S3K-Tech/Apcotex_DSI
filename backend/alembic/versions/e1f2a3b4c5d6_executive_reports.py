"""Quarterly executive reports, management recipients and report email log.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-13

Additive only: three new tables. No existing table or row is changed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "executive_reports" not in existing:
        op.create_table(
            "executive_reports",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("filter_key", sa.String(64), nullable=False),
            sa.Column("filters", postgresql.JSONB(), nullable=False),
            sa.Column("period_label", sa.String(120), nullable=False),
            sa.Column("access_scope", postgresql.JSONB(), nullable=False),
            sa.Column("analytics", postgresql.JSONB(), nullable=False),
            sa.Column("summary", postgresql.JSONB(), nullable=False),
            sa.Column("provider", sa.String(40), nullable=True),
            sa.Column("model", sa.String(255), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by_name", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_executive_reports_created_by", "executive_reports", ["created_by"])
        op.create_index("ix_executive_reports_filter_key", "executive_reports", ["filter_key"])

    if "management_recipients" not in existing:
        op.create_table(
            "management_recipients",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("role_key", sa.String(20), nullable=False, unique=True),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("email", sa.String(320), nullable=True),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_by_name", sa.String(255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "executive_report_emails" not in existing:
        op.create_table(
            "executive_report_emails",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "report_id",
                sa.Integer(),
                sa.ForeignKey("executive_reports.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
            sa.Column("recipients", postgresql.JSONB(), nullable=False),
            sa.Column("subject", sa.String(255), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("attachment_name", sa.String(255), nullable=True),
            sa.Column("attachment_bytes", sa.Integer(), nullable=True),
            sa.Column("sent_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("sent_by_name", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_executive_report_emails_report_id", "executive_report_emails", ["report_id"])


def downgrade() -> None:
    op.drop_table("executive_report_emails")
    op.drop_table("management_recipients")
    op.drop_table("executive_reports")
