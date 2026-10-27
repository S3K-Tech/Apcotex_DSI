"""Chat with Data embeddings table (pgvector).

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-10-15

Requires the ``vector`` extension to be enabled already by a database
administrator (``CREATE EXTENSION vector;``). This migration only checks for
it and never installs it. Vector size 1024 matches Titan Text Embeddings V2
(``BEDROCK_EMBEDDING_DIMENSIONS=1024``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "a7b8c9d0e1f2"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None

_TABLE = "chat_embeddings"
_DIMENSIONS = 1024


def upgrade() -> None:
    conn = op.get_bind()
    installed = conn.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if not installed:
        raise RuntimeError(
            "PostgreSQL extension 'vector' is not enabled in this database. "
            "Ask a database administrator to run CREATE EXTENSION vector; then rerun the migration."
        )
    if _TABLE in set(sa.inspect(conn).get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("doc_type", sa.String(40), nullable=False),
        sa.Column("canonical_type", sa.String(40), nullable=True),
        sa.Column("canonical_id", sa.Integer(), nullable=True),
        sa.Column("canonical_key", sa.String(255), nullable=True),
        sa.Column("scope_distributor_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(_DIMENSIONS), nullable=False),
        sa.Column("embedding_model", sa.String(120), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_chat_embeddings_active_source",
        _TABLE,
        ["doc_type", "source_ref", "embedding_model"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("ix_chat_embeddings_doc_type_active", _TABLE, ["doc_type", "is_active"])
    op.create_index("ix_chat_embeddings_canonical_id", _TABLE, ["canonical_id"])
    op.create_index("ix_chat_embeddings_canonical_key", _TABLE, ["canonical_key"])
    op.create_index("ix_chat_embeddings_created_at", _TABLE, ["created_at"])
    op.execute(
        "CREATE INDEX ix_chat_embeddings_embedding_hnsw ON chat_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chat_embeddings_embedding_hnsw")
    op.drop_index("ix_chat_embeddings_created_at", table_name=_TABLE)
    op.drop_index("ix_chat_embeddings_canonical_key", table_name=_TABLE)
    op.drop_index("ix_chat_embeddings_canonical_id", table_name=_TABLE)
    op.drop_index("ix_chat_embeddings_doc_type_active", table_name=_TABLE)
    op.drop_index("uq_chat_embeddings_active_source", table_name=_TABLE)
    op.drop_table(_TABLE)
