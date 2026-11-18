"""Quarterly executive reports, management recipients and report email sends."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ExecutiveReport(Base):
    """One generated executive summary with the analytics snapshot it interprets."""

    __tablename__ = "executive_reports"
    __table_args__ = (
        Index("ix_executive_reports_created_by", "created_by"),
        Index("ix_executive_reports_filter_key", "filter_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    filters: Mapped[Any] = mapped_column(JSONB, nullable=False)
    period_label: Mapped[str] = mapped_column(String(120), nullable=False)
    access_scope: Mapped[Any] = mapped_column(JSONB, nullable=False)
    analytics: Mapped[Any] = mapped_column(JSONB, nullable=False)
    summary: Mapped[Any] = mapped_column(JSONB, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ManagementRecipient(Base):
    """Configured management address for one role (MD, CFO, CMO, CIO)."""

    __tablename__ = "management_recipients"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_key: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExecutiveReportEmail(Base):
    """One explicit send of a report PDF. ``idempotency_key`` blocks duplicate submits."""

    __tablename__ = "executive_report_emails"
    __table_args__ = (Index("ix_executive_report_emails_report_id", "report_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("executive_reports.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    recipients: Mapped[Any] = mapped_column(JSONB, nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attachment_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sent_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
