"""Sync Chat with Data embeddings (knowledge, distributors, products, aliases).

Calls Amazon Bedrock (paid) for every new or changed document. Never runs
automatically. Requires the chat_embeddings migration.

Usage (from backend/):
    python -m scripts.sync_chat_embeddings --dry-run
    python -m scripts.sync_chat_embeddings --confirm-database <database_name>

``--confirm-database`` must equal the database name in DATABASE_URL.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.database.session import SessionLocal, engine
from app.llm.embedding_client import (
    EmbeddingError,
    EmbeddingResult,
    embed_text,
    embedding_config,
    embedding_usage,
)
from app.services.chat_data.embedding_sync import EmbeddingSyncError, sync_embeddings
from app.services.llm_settings_service import LlmSettingsService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Count changes; no Bedrock calls or writes")
    parser.add_argument("--confirm-database", default="", help="Target database name (required to write)")
    args = parser.parse_args()

    url = engine.url
    print(f"Target database: {url.database} on {url.host}:{url.port} (user {url.username})")
    if not args.dry_run and args.confirm_database != url.database:
        print("Refusing to write: pass --confirm-database matching the target database name.")
        return 2

    try:
        model_id, dims = embedding_config()
    except EmbeddingError as exc:
        print(f"Embedding configuration error: {exc}")
        return 2
    print(f"Embedding model: {model_id} ({dims} dimensions)")

    db = SessionLocal()
    try:
        if not db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
            print("The 'vector' extension is not enabled in this database.")
            return 2
        if not db.execute(text("SELECT to_regclass('public.chat_embeddings')")).scalar():
            print("Table chat_embeddings is missing. Run: alembic upgrade head")
            return 2

        usage_tokens = 0

        def embedder(content: str):
            nonlocal usage_tokens
            result = embed_text(content)
            usage_tokens += result.input_tokens
            return result

        report = sync_embeddings(db, embedder=embedder, model_id=model_id, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()
        else:
            if usage_tokens:
                LlmSettingsService(db).record_usage(
                    purpose="chat_embedding_sync",
                    usage=embedding_usage(EmbeddingResult([], model_id, usage_tokens, 0)),
                    model=model_id,
                    actor="sync_chat_embeddings",
                )
            db.commit()
        print("Dry run (no changes):" if args.dry_run else "Sync complete:")
        print(f"  documents: {report.total_documents} {report.by_type}")
        print(f"  unchanged: {report.unchanged}")
        print(f"  {'would insert' if args.dry_run else 'inserted'}: {report.inserted}")
        print(f"  {'would update' if args.dry_run else 'updated'}: {report.updated}")
        print(f"  {'would deactivate' if args.dry_run else 'deactivated'}: {report.deactivated}")
        if not args.dry_run:
            print(f"  embedding input tokens: {report.input_tokens}")
        return 0
    except EmbeddingSyncError as exc:
        db.rollback()
        print(f"Sync aborted, no changes written: {exc}")
        return 1
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
