"""Append-only audit history for product mapping changes."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ProductMappingHistory(Base):
    """One approval / correction / conflict event for a product mapping."""

    __tablename__ = "product_mapping_history"
    __table_args__ = (
        Index("ix_product_mapping_history_mapping_id", "mapping_id"),
        Index("ix_product_mapping_history_normalized", "normalized_original_name"),
        Index("ix_product_mapping_history_distributor_id", "distributor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mapping_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("product_alias_mappings.id", ondelete="SET NULL"), nullable=True
    )
    original_product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    distributor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attachment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    report_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    master_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
