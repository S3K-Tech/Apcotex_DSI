"""pgvector sync idempotency and scope-filtered retrieval (stub embedder, no AWS calls)."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select

from app.llm.embedding_client import EMBEDDING_DIMENSIONS, EmbeddingError, EmbeddingResult
from app.models.distributor import Distributor
from app.services.chat_data.embedding_sync import (
    EmbeddingSyncError,
    SyncDocument,
    sync_embeddings,
)
from app.services.chat_data.retrieval_service import DOC_DISTRIBUTOR, RetrievalService

MODEL = "amazon.titan-embed-text-v2:0"


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    vec = [0.0] * EMBEDDING_DIMENSIONS
    for i, b in enumerate(digest):
        vec[(b * 7 + i) % EMBEDDING_DIMENSIONS] += 1.0 + b / 255.0
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


class StubEmbedder:
    def __init__(self, fail_on: str | None = None):
        self.calls = 0
        self.fail_on = fail_on

    def __call__(self, text: str) -> EmbeddingResult:
        self.calls += 1
        if self.fail_on and self.fail_on in text:
            raise EmbeddingError("stub failure")
        return EmbeddingResult(vector=_vector(text), model_id=MODEL, input_tokens=3, latency_ms=1)


def _active(db):
    from app.models.chat_embedding import ChatEmbedding

    return db.scalars(
        select(ChatEmbedding).where(ChatEmbedding.is_active.is_(True), ChatEmbedding.embedding_model == MODEL)
    ).all()


def _docs(*names):
    return [
        SyncDocument(DOC_DISTRIBUTOR, f"distributor:{i}", f"Distributor: {n}", "distributor", i, n.upper())
        for i, n in names
    ]


def test_sync_is_idempotent_and_deactivates_stale(vector_db):
    db = vector_db
    stub = StubEmbedder()
    first = sync_embeddings(db, embedder=stub, model_id=MODEL, documents=_docs((1, "Alpha"), (2, "Beta")))
    assert (first.inserted, first.unchanged, stub.calls) == (2, 0, 2)

    again = sync_embeddings(db, embedder=stub, model_id=MODEL, documents=_docs((1, "Alpha"), (2, "Beta")))
    assert (again.inserted, again.updated, again.unchanged, stub.calls) == (0, 0, 2, 2)

    changed = sync_embeddings(db, embedder=stub, model_id=MODEL, documents=_docs((1, "Alpha Renamed")))
    assert (changed.updated, changed.deactivated) == (1, 1)
    active = _active(db)
    assert [r.source_ref for r in active] == ["distributor:1"]
    assert active[0].content == "Distributor: Alpha Renamed"

    from app.models.chat_embedding import ChatEmbedding

    dupes = db.execute(
        select(ChatEmbedding.source_ref, func.count())
        .where(ChatEmbedding.is_active.is_(True))
        .group_by(ChatEmbedding.source_ref)
        .having(func.count() > 1)
    ).all()
    assert dupes == []


def test_sync_failure_changes_nothing(vector_db):
    db = vector_db
    sync_embeddings(db, embedder=StubEmbedder(), model_id=MODEL, documents=_docs((1, "Alpha"), (2, "Beta")))
    before = sorted((r.source_ref, r.content_hash) for r in _active(db))
    with pytest.raises(EmbeddingSyncError):
        sync_embeddings(
            db,
            embedder=StubEmbedder(fail_on="Gamma"),
            model_id=MODEL,
            documents=_docs((1, "Alpha v2"), (3, "Gamma")),
        )
    assert sorted((r.source_ref, r.content_hash) for r in _active(db)) == before


def test_build_documents_never_embeds_sales_or_customers(vector_db):
    from app.services.chat_data.embedding_sync import build_documents

    db = vector_db
    db.add(Distributor(name="Delta Dist", company="Delta Dist"))
    db.flush()
    docs = build_documents(db)
    assert {d.doc_type for d in docs} <= {"knowledge", "distributor", "product", "product_alias"}
    assert any(d.content.startswith("Distributor: Delta Dist") for d in docs)


def test_retrieval_filters_by_scope_before_ranking(vector_db):
    db = vector_db
    sync_embeddings(
        db,
        embedder=StubEmbedder(),
        model_id=MODEL,
        documents=_docs((1, "Alpha Traders"), (2, "Gamma Secret Corp")),
    )
    retrieval = RetrievalService(db, embedder=StubEmbedder(), enabled=True)
    query = "Distributor: Gamma Secret Corp"
    unrestricted = retrieval.search(query, doc_types=[DOC_DISTRIBUTOR], model_id=MODEL, allowed_ids=[1, 2], max_distance=2)
    assert unrestricted[0].canonical_id == 2
    scoped = retrieval.search(query, doc_types=[DOC_DISTRIBUTOR], model_id=MODEL, allowed_ids=[1], max_distance=2)
    assert all(hit.canonical_id == 1 for hit in scoped)
    assert retrieval.search(query, doc_types=[DOC_DISTRIBUTOR], model_id=MODEL, allowed_ids=[]) == []


def test_retrieval_survives_embedding_failure(vector_db):
    retrieval = RetrievalService(vector_db, embedder=StubEmbedder(fail_on="x"), enabled=True)
    assert retrieval.search("x", doc_types=[DOC_DISTRIBUTOR], model_id=MODEL) == []
