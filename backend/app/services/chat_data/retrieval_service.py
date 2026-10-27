"""Scope-filtered pgvector retrieval for Chat with Data.

Authorization filters (allowed ids / keys) are applied in SQL before ranking,
so unauthorized documents are never candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.embedding_client import EmbeddingError, EmbeddingResult, embed_text

logger = get_logger(__name__)

DOC_KNOWLEDGE = "knowledge"
DOC_DISTRIBUTOR = "distributor"
DOC_PRODUCT = "product"
DOC_PRODUCT_ALIAS = "product_alias"

Embedder = Callable[[str], EmbeddingResult]


@dataclass
class RetrievedDoc:
    doc_type: str
    canonical_id: Optional[int]
    canonical_key: Optional[str]
    content: str
    distance: float


class RetrievalService:
    def __init__(self, db: Session, *, embedder: Optional[Embedder] = None, enabled: Optional[bool] = None):
        self.db = db
        self.embedder = embedder or embed_text
        self.enabled = get_settings().chat_semantic_search_enabled if enabled is None else enabled
        self._query_cache: dict[str, Optional[List[float]]] = {}
        self.last_embedding: Optional[EmbeddingResult] = None

    def _vector(self, text: str) -> Optional[List[float]]:
        if text in self._query_cache:
            return self._query_cache[text]
        vector: Optional[List[float]] = None
        try:
            result = self.embedder(text)
            self.last_embedding = result
            vector = result.vector
        except EmbeddingError as exc:
            logger.warning("Chat retrieval embedding failed: {}", exc)
        self._query_cache[text] = vector
        return vector

    def search(
        self,
        text: str,
        *,
        doc_types: Sequence[str],
        model_id: str,
        allowed_ids: Optional[Sequence[int]] = None,
        allowed_keys: Optional[Sequence[str]] = None,
        allowed_scope_distributor_ids: Optional[Sequence[int]] = None,
        k: int = 5,
        max_distance: float = 0.6,
    ) -> List[RetrievedDoc]:
        """Nearest documents among the caller's authorized candidates only.

        ``allowed_ids`` / ``allowed_keys``: ``None`` = no restriction on that axis,
        empty = nothing allowed (returns no results without querying).
        """
        if not self.enabled or not (text or "").strip():
            return []
        if allowed_ids is not None and not allowed_ids:
            return []
        if allowed_keys is not None and not allowed_keys:
            return []
        vector = self._vector(text)
        if vector is None:
            return []
        from app.models.chat_embedding import ChatEmbedding

        distance = ChatEmbedding.embedding.cosine_distance(vector)
        q = select(
            ChatEmbedding.doc_type,
            ChatEmbedding.canonical_id,
            ChatEmbedding.canonical_key,
            ChatEmbedding.content,
            distance.label("distance"),
        ).where(
            ChatEmbedding.is_active.is_(True),
            ChatEmbedding.embedding_model == model_id,
            ChatEmbedding.doc_type.in_(list(doc_types)),
        )
        if allowed_ids is not None:
            q = q.where(ChatEmbedding.canonical_id.in_([int(i) for i in allowed_ids]))
        if allowed_keys is not None:
            q = q.where(ChatEmbedding.canonical_key.in_(list(allowed_keys)))
        if allowed_scope_distributor_ids is not None:
            q = q.where(
                or_(
                    ChatEmbedding.scope_distributor_id.is_(None),
                    ChatEmbedding.scope_distributor_id.in_(
                        [int(i) for i in allowed_scope_distributor_ids] or [-1]
                    ),
                )
            )
        q = q.where(distance <= max_distance).order_by(distance).limit(max(1, min(int(k), 20)))
        try:
            with self.db.begin_nested():
                rows = self.db.execute(q).all()
        except SQLAlchemyError as exc:
            logger.warning("Chat retrieval query failed: {}", type(exc).__name__)
            return []
        return [
            RetrievedDoc(r.doc_type, r.canonical_id, r.canonical_key, r.content, float(r.distance))
            for r in rows
        ]
