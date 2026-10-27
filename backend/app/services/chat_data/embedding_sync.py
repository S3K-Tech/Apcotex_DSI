"""Idempotent sync of approved Chat with Data documents into ``chat_embeddings``.

Embeds only: business knowledge, distributor names, canonical products and
active product aliases. Never sales rows or customers.

All vectors are computed before any write. If one embedding fails, nothing is
changed. Unchanged documents (same content hash and model) are skipped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.llm.embedding_client import EmbeddingError, EmbeddingResult
from app.models.chat_embedding import ChatEmbedding
from app.models.distributor import Distributor
from app.models.product_alias_mapping import ProductAliasMapping
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.services.chat_data.knowledge import KNOWLEDGE_DOCUMENTS
from app.services.chat_data.retrieval_service import (
    DOC_DISTRIBUTOR,
    DOC_KNOWLEDGE,
    DOC_PRODUCT,
    DOC_PRODUCT_ALIAS,
)
from app.services.product_standardization import canonical_products, normalize_product_key
from app.utils.distributor_name import normalize_distributor_name


class EmbeddingSyncError(Exception):
    """Sync aborted before any database change."""


@dataclass(frozen=True)
class SyncDocument:
    doc_type: str
    source_ref: str
    content: str
    canonical_type: Optional[str] = None
    canonical_id: Optional[int] = None
    canonical_key: Optional[str] = None
    scope_distributor_id: Optional[int] = None

    @property
    def identity(self) -> Tuple[str, str]:
        return self.doc_type, self.source_ref

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            [
                self.doc_type,
                self.source_ref,
                self.content,
                self.canonical_type,
                self.canonical_id,
                self.canonical_key,
                self.scope_distributor_id,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class SyncReport:
    total_documents: int = 0
    unchanged: int = 0
    inserted: int = 0
    updated: int = 0
    deactivated: int = 0
    input_tokens: int = 0
    dry_run: bool = False
    by_type: Dict[str, int] = field(default_factory=dict)


def build_documents(db: Session) -> List[SyncDocument]:
    docs: List[SyncDocument] = []
    for key, text in KNOWLEDGE_DOCUMENTS:
        docs.append(SyncDocument(DOC_KNOWLEDGE, f"knowledge:{key}", text, "knowledge", None, key))

    distributors = db.execute(
        select(Distributor.id, Distributor.name, Distributor.company, Distributor.code).where(
            Distributor.is_deleted.is_(False)
        )
    ).all()
    for dist_id, name, company, code in distributors:
        primary = " ".join(str(company or name or "").split())
        if not primary:
            continue
        parts = [f"Distributor: {primary}"]
        other = " ".join(str(name or "").split())
        if other and other.casefold() != primary.casefold():
            parts.append(f"Also known as: {other}")
        if code:
            parts.append(f"Code: {code}")
        docs.append(
            SyncDocument(
                DOC_DISTRIBUTOR,
                f"distributor:{dist_id}",
                ". ".join(parts),
                "distributor",
                int(dist_id),
                normalize_distributor_name(primary),
            )
        )

    raw_products = db.scalars(
        select(distinct(SalesRecord.product))
        .join(Report, Report.id == SalesRecord.report_id)
        .where(
            SalesRecord.is_deleted.is_(False),
            Report.is_deleted.is_(False),
            SalesRecord.product.is_not(None),
        )
    ).all()
    for product in canonical_products([p for p in raw_products if p]):
        key = normalize_product_key(product)
        docs.append(
            SyncDocument(DOC_PRODUCT, f"product:{key}", f"Product: {product}", "product", None, key)
        )

    aliases = db.execute(
        select(
            ProductAliasMapping.id,
            ProductAliasMapping.distributor_id,
            ProductAliasMapping.original_product_name,
            ProductAliasMapping.normalized_product_name,
        ).where(ProductAliasMapping.is_active.is_(True))
    ).all()
    for alias_id, dist_id, original, normalized in aliases:
        key = normalize_product_key(normalized or "")
        if not key or not (original or "").strip():
            continue
        docs.append(
            SyncDocument(
                DOC_PRODUCT_ALIAS,
                f"product_alias:{alias_id}",
                f"Product alias: {original} means {normalized}",
                "product",
                None,
                key,
                int(dist_id) if dist_id else None,
            )
        )

    unique: Dict[Tuple[str, str], SyncDocument] = {}
    for doc in docs:
        unique[doc.identity] = doc
    return list(unique.values())


def sync_embeddings(
    db: Session,
    *,
    embedder: Callable[[str], EmbeddingResult],
    model_id: str,
    dry_run: bool = False,
    documents: Optional[List[SyncDocument]] = None,
) -> SyncReport:
    """Upsert changed documents, deactivate stale ones. Caller commits."""
    docs = documents if documents is not None else build_documents(db)
    report = SyncReport(total_documents=len(docs), dry_run=dry_run)
    for doc in docs:
        report.by_type[doc.doc_type] = report.by_type.get(doc.doc_type, 0) + 1

    existing_rows = db.scalars(
        select(ChatEmbedding).where(
            ChatEmbedding.is_active.is_(True),
            ChatEmbedding.embedding_model == model_id,
        )
    ).all()
    existing: Dict[Tuple[str, str], ChatEmbedding] = {}
    for row in existing_rows:
        key = (row.doc_type, row.source_ref)
        if key in existing:
            if not dry_run:
                row.is_active = False
            report.deactivated += 1
            continue
        existing[key] = row

    wanted = {doc.identity for doc in docs}
    to_embed: List[SyncDocument] = []
    for doc in docs:
        row = existing.get(doc.identity)
        if row is not None and row.content_hash == doc.content_hash:
            report.unchanged += 1
        else:
            to_embed.append(doc)
    stale = [row for key, row in existing.items() if key not in wanted]

    if dry_run:
        report.inserted = sum(1 for d in to_embed if d.identity not in existing)
        report.updated = len(to_embed) - report.inserted
        report.deactivated += len(stale)
        return report

    vectors: Dict[Tuple[str, str], List[float]] = {}
    for doc in to_embed:
        try:
            result = embedder(doc.content)
        except EmbeddingError as exc:
            raise EmbeddingSyncError(f"Embedding failed for {doc.source_ref}: {exc}") from exc
        if result.model_id != model_id:
            raise EmbeddingSyncError("Embedder returned a different model id")
        vectors[doc.identity] = result.vector
        report.input_tokens += int(result.input_tokens or 0)

    for doc in to_embed:
        row = existing.get(doc.identity)
        if row is not None:
            row.is_active = False
            report.updated += 1
        else:
            report.inserted += 1
    for row in stale:
        row.is_active = False
        report.deactivated += 1
    db.flush()

    for doc in to_embed:
        db.add(
            ChatEmbedding(
                doc_type=doc.doc_type,
                canonical_type=doc.canonical_type,
                canonical_id=doc.canonical_id,
                canonical_key=doc.canonical_key,
                scope_distributor_id=doc.scope_distributor_id,
                content=doc.content,
                content_hash=doc.content_hash,
                embedding=vectors[doc.identity],
                embedding_model=model_id,
                source_ref=doc.source_ref,
                is_active=True,
            )
        )
    db.flush()
    return report
