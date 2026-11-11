"""Distributor raw customer name → standardized customer name."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.distributor import Distributor


class CustomerAliasMapping(Base, TimestampMixin):
    """
    One customer name as a distributor sent it, and its approved standard name.

    ``distributor_id`` NULL is a global mapping; a distributor-specific row wins
    over a global row for the same normalized name. Only one active row may
    exist per (distributor, key) and per global key.
    """

    __tablename__ = "customer_alias_mappings"
    __table_args__ = (
        Index(
            "uq_customer_alias_mappings_dist_key_active",
            "distributor_id",
            "normalized_original_name",
            unique=True,
            postgresql_where=text("is_active = true AND distributor_id IS NOT NULL"),
        ),
        Index(
            "uq_customer_alias_mappings_global_key_active",
            "normalized_original_name",
            unique=True,
            postgresql_where=text("is_active = true AND distributor_id IS NULL"),
        ),
        Index("ix_customer_alias_mappings_distributor_id", "distributor_id"),
        Index("ix_customer_alias_mappings_standardized", "standardized_customer_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    distributor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True
    )
    original_customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # customer_standardization.normalize.mapping_key(original_customer_name)
    normalized_original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    standardized_customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="GLOBAL", server_default="GLOBAL")
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="APPROVED", server_default="APPROVED"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_new_customer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    distributor: Mapped[Optional["Distributor"]] = relationship("Distributor")

    def __repr__(self) -> str:
        return (
            f"<CustomerAliasMapping id={self.id} distributor_id={self.distributor_id} "
            f"original={self.original_customer_name!r} standard={self.standardized_customer_name!r}>"
        )
