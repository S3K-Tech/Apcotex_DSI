"""Batch pending Product/Customer counts for Emails list badges."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.customer_master import CustomerMaster
from app.models.distributor import Distributor
from app.models.email_message import EmailAttachment, EmailMessage
from app.models.product_standardization_snapshot import ProductStandardizationSnapshot
from app.services.standardization_pending import StandardizationPendingService


@pytest.fixture()
def dist(db):
    d = Distributor(name="Pending Dist", company="Pending Dist")
    db.add(d)
    db.flush()
    return d


def _email_with_snap(db, dist, rows, *, graph="g-pending"):
    email = EmailMessage(
        graph_message_id=graph,
        subject=f"{dist.company} | Mumbai | Paper",
        sender_email="pending@test",
        received_at=datetime.now(timezone.utc),
        subject_valid=True,
        parsed_distributor=dist.company,
    )
    db.add(email)
    db.flush()
    att = EmailAttachment(
        graph_attachment_id=f"att-{graph}",
        file_name="sales.xlsx",
        file_path="/tmp/missing-not-parsed.xlsx",
        email_message_id=email.id,
        content_hash="hash1",
        is_excel=True,
    )
    db.add(att)
    db.flush()
    snap = ProductStandardizationSnapshot(
        email_id=email.id,
        attachment_id=att.id,
        attachment_name="sales.xlsx",
        content_hash="hash1",
        distributor_id=dist.id,
        row_count=len(rows),
        columns=["customer_name", "product", "sales_quantity"],
        rows=rows,
        source_table={"available": False},
    )
    db.add(snap)
    db.flush()
    return email


def test_pending_counts_use_workbench_buckets_without_parsing(db, dist):
    db.add(CustomerMaster(customer_name="Known Customer Ltd", segment="Paper"))
    db.flush()
    email = _email_with_snap(
        db,
        dist,
        [
            {"customer_name": "Known Customer Ltd", "product": "Mystery Product A", "sales_quantity": 1},
            {"customer_name": "Known Customer Ltd", "product": "Mystery Product A", "sales_quantity": 2},
            {"customer_name": "Brand New Buyer", "product": "Mystery Product B", "sales_quantity": 3},
            {"customer_name": "Typo Nam", "product": "Mystery Product C", "sales_quantity": 1},
        ],
    )
    other = EmailMessage(
        graph_message_id="g-nosnap",
        subject=f"{dist.company} | Mumbai | Paper",
        sender_email="pending@test",
        received_at=datetime.now(timezone.utc),
        subject_valid=True,
        parsed_distributor=dist.company,
    )
    db.add(other)
    db.flush()

    items = StandardizationPendingService(db).counts_for_emails([email.id, other.id, 999999])
    by_id = {i["email_id"]: i for i in items}
    assert by_id[email.id]["available"] is True
    # Distinct products all unmapped → 3 pending (duplicate Mystery Product A counted once)
    assert by_id[email.id]["products_pending"] == 3
    # Known master exact is auto_matched; Brand New Buyer + Typo Nam pending → 2
    assert by_id[email.id]["customers_pending"] == 2
    assert by_id[other.id] == {
        "email_id": other.id,
        "products_pending": None,
        "customers_pending": None,
        "available": False,
    }
    assert by_id[999999]["available"] is False


def test_pending_endpoint_returns_batch(db, dist, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import outlook as outlook_mod
    from app.database.session import get_db
    from app.dependencies.rbac import RequestUser, get_current_user
    from app.enums import OutlookSyncPermission, UserRole
    from app.exceptions import AppException, app_exception_handler

    email = _email_with_snap(
        db,
        dist,
        [{"customer_name": "Only Buyer", "product": "Only Product", "sales_quantity": 1}],
        graph="g-api",
    )

    monkeypatch.setattr(
        outlook_mod,
        "_resolve_outlook_sync_permission",
        lambda current, _db: OutlookSyncPermission.ALL.value,
    )

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(outlook_mod.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: RequestUser(
        role=UserRole.SUPER_ADMIN, name="super", user_id=None
    )
    client = TestClient(app)
    r = client.post("/api/emails/standardization-pending", json={"email_ids": [email.id]})

    assert r.status_code == 200, r.text
    item = r.json()["data"]["items"][0]
    assert item["available"] is True
    assert item["products_pending"] == 1
    assert item["customers_pending"] == 1
