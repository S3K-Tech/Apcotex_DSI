"""pgvector embeddings for Chat with Data retrieval (glossary, distributors, products).

Never stores sales rows or customer data.
"""

from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.llm.embedding_client import EMBEDDING_DIMENSIONS


class ChatEmbedding(Base, TimestampMixin):
    __tablename__ = "chat_embeddings"
    __table_args__ = (
        Index(
            "uq_chat_embeddings_active_source",
            "doc_type",
            "source_ref",
            "embedding_model",
            unique=True,
            postgresql_where="is_active = true",
        ),
        Index("ix_chat_embeddings_doc_type_active", "doc_type", "is_active"),
        Index("ix_chat_embeddings_canonical_id", "canonical_id"),
        Index("ix_chat_embeddings_canonical_key", "canonical_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    canonical_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    canonical_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scope_distributor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[List[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    def __repr__(self) -> str:
        return f"<ChatEmbedding id={self.id} {self.doc_type}:{self.source_ref}>"
