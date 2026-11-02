"""FG Master (finished goods) versions and their Material Descriptions."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class FGMasterVersion(Base, TimestampMixin):
    """
    One uploaded FG Master workbook.

    ``draft`` versions are validated but unpublished. Exactly one version can be
    ``active``; publishing a draft marks the previous active one ``superseded``.
    Rows are never hard-deleted so every published version stays auditable.
    """

    __tablename__ = "fg_master_versions"
    __table_args__ = (
        UniqueConstraint("version_number", name="uq_fg_master_versions_number"),
        Index(
            "uq_fg_master_versions_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sheet_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    products: Mapped[List["FGMasterProduct"]] = relationship(
        "FGMasterProduct",
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<FGMasterVersion id={self.id} v={self.version_number} status={self.status}>"


class FGMasterProduct(Base, TimestampMixin):
    """One distinct, validated Material Description inside a master version."""

    __tablename__ = "fg_master_products"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "normalized_description",
            name="uq_fg_master_products_version_normalized",
        ),
        Index("ix_fg_master_products_version_id", "version_id"),
        Index("ix_fg_master_products_normalized", "normalized_description"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fg_master_versions.id", ondelete="CASCADE"), nullable=False
    )
    material_description: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_description: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    version: Mapped[FGMasterVersion] = relationship("FGMasterVersion", back_populates="products")
