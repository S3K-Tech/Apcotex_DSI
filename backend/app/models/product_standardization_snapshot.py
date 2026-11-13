"""Cached extracted rows of one email attachment, for the standardization workbench."""

from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ProductStandardizationSnapshot(Base, TimestampMixin):
    """
    Parser output for one attachment, stored so the workbench and the full
    preview do not re-run the parser on every open. Refreshed when the
    attachment content hash changes. Never used to write sales records.
    """

    __tablename__ = "product_standardization_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "email_id", "attachment_id", name="uq_product_std_snapshots_email_attachment"
        ),
        Index("ix_product_std_snapshots_email_id", "email_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False
    )
    attachment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_attachments.id", ondelete="CASCADE"), nullable=False
    )
    attachment_name: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    distributor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parser_used: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    columns: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    rows: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    # Original worksheet header + rows in source order (see product_master.source_table).
    # NULL = snapshot built before this existed; {"available": false} = not readable.
    source_table: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
