"""Test isolation: every test runs against a disposable PostgreSQL database.

``DATABASE_URL`` is redirected to ``<server>/apcotex_phase2a_test`` before any ``app``
module is imported, so neither the app engine nor ``SessionLocal`` can reach the
working database named in ``backend/.env``. The test database is created from the ORM
metadata (no Alembic) and dropped when the session ends.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DB_NAME = "apcotex_phase2a_test"

_configured = os.environ.get("DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get(
    "DATABASE_URL"
)
if not _configured:
    raise RuntimeError("DATABASE_URL is not configured; cannot derive the test database URL")
_working_url = make_url(_configured)
if _working_url.database == TEST_DB_NAME:
    raise RuntimeError("Configured DATABASE_URL already points at the test database name")
TEST_URL = _working_url.set(database=TEST_DB_NAME)
assert TEST_URL.database == TEST_DB_NAME and TEST_URL.database != _working_url.database
os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)


def _admin_engine():
    return create_engine(
        _working_url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True
    )


@pytest.fixture(scope="session")
def test_engine():
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))

    import app.models  # noqa: F401  (register every table)
    from app.database.base import Base
    from app.database.session import engine

    assert engine.url.database == TEST_DB_NAME
    # Some models declare the same index twice (column index=True plus an explicit
    # Index). Alembic builds the real schema; for create_all keep one per name.
    for table in Base.metadata.sorted_tables:
        seen: set = set()
        for index in sorted(table.indexes, key=lambda i: str(i.name)):
            if index.name in seen:
                table.indexes.discard(index)
            else:
                seen.add(index.name)
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def db(test_engine):
    """One session per test; everything is rolled back afterwards."""
    from sqlalchemy.orm import Session

    connection = test_engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
