"""Customer Standardization Workbench: matching, approval/learning, ingestion apply, API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.dependencies.rbac import RequestUser
from app.enums import UserRole
from app.exceptions import ConflictError, ValidationAppError
from app.models.customer_alias_mapping import CustomerAliasMapping
from app.models.customer_mapping_history import CustomerMappingHistory
from app.models.customer_master import CustomerMaster
from app.models.distributor import Distributor
from app.models.email_message import EmailAttachment, EmailMessage
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.services.customer_standardization.apply import apply_customer_standardization
from app.services.customer_standardization.learning_service import CustomerLearningService
from app.services.customer_standardization.matching import (
    AUTO_MATCHED,
    METHOD_DISTRIBUTOR_MAPPING,
    METHOD_EXISTING_EXACT,
    METHOD_GLOBAL_MAPPING,
    METHOD_MASTER_EXACT,
    NEEDS_REVIEW,
    UNMAPPED,
    CustomerMatcher,
    similarity,
)
from app.services.customer_standardization.normalize import customer_key, mapping_key
from app.services.incremental_upload import EXACT, NEW, analyse_rows

SUPER = RequestUser(role=UserRole.SUPER_ADMIN, name="super", user_id=None)


@pytest.fixture()
def dists(db):
    a = Distributor(name="Alpha Dist", company="Alpha Dist")
    b = Distributor(name="Beta Dist", company="Beta Dist")
    c = Distributor(name="Gamma Dist", company="Gamma Dist")
    db.add_all([a, b, c])
    db.flush()
    return a, b, c


def _master(db, *names, segment=None):
    for name in names:
        db.add(CustomerMaster(customer_name=name, segment=segment))
    db.flush()


def _sale(db, dist, customer, *, product="AB820", qty="5", segment="Paper", original=None):
    report = db.scalar(select(Report).where(Report.distributor_id == dist.id))
    if report is None:
        report = Report(name="hist", reporting_month="FY 2025-26 • Q4", distributor_id=dist.id, status="completed")
        db.add(report)
        db.flush()
    rec = SalesRecord(
        customer_name=customer, segment=segment, product=product, quantity=Decimal(qty),
        period="FY 2025-26 • Q4", source_month="March 2026", location="", unit="MT",
        row_hash=f"h-{customer}-{product}", report_id=report.id, distributor_id=dist.id,
        original_customer_name=original,
    )
    db.add(rec)
    db.flush()
    return rec


def _approve(db, dist, raw, standard, **kw):
    item = {"raw_customer": raw, "standard_customer": standard, **kw}
    return CustomerLearningService(db).approve([item], SUPER, distributor_id=dist.id)


# ------------------------------------------------------------------ normalize
def test_customer_key_canonicalizes_legal_forms_and_prefixes():
    assert customer_key("M/s. Abc Rubber Pvt. Ltd.") == "ABC RUBBER PRIVATE LIMITED"
    assert customer_key("ABC RUBBER PRIVATE LIMITED") == "ABC RUBBER PRIVATE LIMITED"
    assert customer_key("abc rubber pvtltd") == "ABC RUBBER PRIVATE LIMITED"
    assert customer_key("The Kumar & Sons Co.") == "KUMAR AND SONS COMPANY"
    assert mapping_key("A.B.C. Rubber") == mapping_key("ABC Rubber")
    assert customer_key(None) == "" and mapping_key("   ") == ""
    # Different legal entities keep different keys.
    assert mapping_key("ABC Polymers Ltd") != mapping_key("ABC Polymers Pvt Ltd")
    assert mapping_key("ABC Polymers LLP") != mapping_key("ABC Polymers Pvt Ltd")


def test_similarity_is_conservative_about_generic_words_and_legal_entities():
    # Shared generic words only: never a strong match.
    assert similarity("ABC RUBBER INDUSTRIES PVT LTD", "XYZ RUBBER INDUSTRIES PVT LTD") <= 60
    assert similarity("RUBBER INDUSTRIES", "RUBBER INDUSTRIES LTD") <= 50
    assert similarity("Shree Ganesh Rubber", "Shree Ram Rubber") <= 60
    # Legal entity differences are respected.
    assert similarity("ABC Polymers Pvt Ltd", "ABC Polymers Ltd") <= 70
    assert similarity("ABC Polymers LLP", "ABC Polymers Pvt Ltd") <= 70
    # Unit numbers must agree.
    assert similarity("Apex Unit 1", "Apex Unit 2") <= 60
    # Misspellings of the same customer stay suggestible.
    assert similarity("Shree Ganesh Ruber Industries", "SHREE GANESH RUBBER INDUSTRIES") >= 90
    assert similarity("Sundaram Industries", "Sundram Industries") >= 90
    assert 80 <= similarity("Kumar Rubber Products", "Kumar Rubber Products Pvt Ltd") < 90


# ------------------------------------------------------------------ matching
def test_empty_registry_marks_everything_unmapped(db, dists):
    a, _b, _c = dists
    m = CustomerMatcher.load(db)
    result = m.match("Brand New Customer Pvt Ltd", a.id)
    assert result.status == UNMAPPED and result.suggested is None
    assert m.match("   ", a.id).status == UNMAPPED
    assert m.match(None, a.id).status == UNMAPPED


def test_distributor_mapping_beats_global_and_global_is_reused(db, dists):
    a, b, c = dists
    _approve(db, a, "ABC Rubr", "ABC RUBBER PRIVATE LIMITED")  # creates distributor + global
    _approve(db, b, "ABC Rubr", "ABC RUBBER WORKS")  # global kept; stays Beta-specific
    m = CustomerMatcher.load(db)
    ra, rb, rc = m.match("ABC Rubr", a.id), m.match("abc  rubr", b.id), m.match("ABC RUBR", c.id)
    assert (ra.method, ra.suggested) == (METHOD_DISTRIBUTOR_MAPPING, "ABC RUBBER PRIVATE LIMITED")
    assert (rb.method, rb.suggested) == (METHOD_DISTRIBUTOR_MAPPING, "ABC RUBBER WORKS")
    assert (rc.method, rc.suggested, rc.status) == (METHOD_GLOBAL_MAPPING, "ABC RUBBER PRIVATE LIMITED", AUTO_MATCHED)
    assert ra.approved and rc.approved


def test_exact_master_and_existing_names_auto_match(db, dists):
    a, _b, _c = dists
    _master(db, "Kumar Rubber Products Private Limited")
    _sale(db, a, "Zen Polymers Ltd")
    m = CustomerMatcher.load(db)
    r = m.match("M/s Kumar Rubber Products Pvt. Ltd.", a.id)
    assert (r.status, r.method, r.suggested) == (AUTO_MATCHED, METHOD_MASTER_EXACT, "Kumar Rubber Products Private Limited")
    r = m.match("ZEN POLYMERS LIMITED", a.id)
    assert (r.status, r.method, r.suggested) == (AUTO_MATCHED, METHOD_EXISTING_EXACT, "Zen Polymers Ltd")


def test_several_existing_spellings_need_review(db, dists):
    a, _b, _c = dists
    _sale(db, a, "Abc Pvt Ltd", product="P1")
    _sale(db, a, "ABC Private Limited", product="P2")
    _sale(db, a, "ABC Private Limited", product="P3")
    m = CustomerMatcher.load(db)
    r = m.match("abc pvt. ltd.", a.id)
    assert r.status == NEEDS_REVIEW and r.suggested == "ABC Private Limited"
    assert {alt["name"] for alt in r.alternatives} == {"Abc Pvt Ltd", "ABC Private Limited"}


def test_fuzzy_is_suggestion_only_and_conservative(db, dists):
    a, _b, _c = dists
    _master(db, "Shree Ganesh Rubber Industries", "XYZ Rubber Industries Pvt Ltd", "ABC Polymers Ltd")
    m = CustomerMatcher.load(db)
    r = m.match("Shree Ganesh Ruber Industries", a.id)  # misspelling
    assert r.status == NEEDS_REVIEW and r.suggested == "Shree Ganesh Rubber Industries"
    r = m.match("ABC Rubber Industries Pvt Ltd", a.id)  # shares only generic words with XYZ
    assert r.status == UNMAPPED and r.suggested is None
    r = m.match("ABC Polymers Pvt Ltd", a.id)  # different legal entity
    assert r.status == UNMAPPED
    r = m.match("Totally Different Name", a.id)
    assert r.status == UNMAPPED and r.alternatives == []


def test_segment_scope_limits_reference_names(db, dists):
    a, _b, _c = dists
    _sale(db, a, "Paper Only Customer", segment="Paper")
    _sale(db, a, "Latex Only Customer", segment="Latex")
    names = {i["name"] for i in CustomerMatcher.load(db, segments=["Paper"]).search("only")}
    assert names == {"Paper Only Customer"}
    assert CustomerMatcher.load(db, segments=[]).search("only") == []


# ------------------------------------------------------------------ learning
def test_approval_creates_distributor_and_global_mapping_with_history(db, dists):
    a, _b, _c = dists
    res = _approve(db, a, "Kumar Rbr", "KUMAR RUBBER PRODUCTS", confidence=82)
    assert res["saved"] == 1 and res["conflicts"] == []
    r = res["results"][0]
    assert r["distributor_action"] == "created" and r["global_action"] == "created" and r["is_new_customer"] is True
    rows = list(db.scalars(select(CustomerAliasMapping).order_by(CustomerAliasMapping.id)))
    assert [(m.scope, m.distributor_id) for m in rows] == [("DISTRIBUTOR", a.id), ("GLOBAL", None)]
    assert all(m.original_customer_name == "Kumar Rbr" and m.normalized_original_name == "KUMARRBR" for m in rows)
    assert all(m.status == "APPROVED" and m.approved_by_name == "super" and m.approved_at for m in rows)
    assert db.scalar(select(func.count()).select_from(CustomerMappingHistory)) == 2


def test_approval_snaps_to_existing_spelling(db, dists):
    a, _b, _c = dists
    _master(db, "Kumar Rubber Products Private Limited")
    res = _approve(db, a, "Kumar Rbr", "kumar rubber products pvt ltd")
    assert res["results"][0]["standard_customer"] == "Kumar Rubber Products Private Limited"
    assert res["results"][0]["is_new_customer"] is False


def test_reapproval_with_same_value_is_unchanged(db, dists):
    a, _b, _c = dists
    _approve(db, a, "Kumar Rbr", "KUMAR RUBBER")
    res = _approve(db, a, "KUMAR  RBR", "kumar rubber")
    assert res["results"][0]["distributor_action"] == "unchanged" and res["saved"] == 0
    assert db.scalar(select(func.count()).select_from(CustomerAliasMapping)) == 2


def test_conflicting_approval_is_not_saved_without_explicit_replace(db, dists):
    a, _b, _c = dists
    _approve(db, a, "Kumar Rbr", "KUMAR RUBBER")
    res = _approve(db, a, "Kumar Rbr", "KUMAR POLYMERS")
    assert res["saved"] == 0 and len(res["conflicts"]) == 1
    assert res["conflicts"][0]["existing_customer"] == "KUMAR RUBBER"
    active = db.scalar(select(CustomerAliasMapping).where(
        CustomerAliasMapping.distributor_id == a.id, CustomerAliasMapping.is_active.is_(True)))
    assert active.standardized_customer_name == "KUMAR RUBBER"
    assert db.scalar(select(func.count()).select_from(CustomerMappingHistory).where(
        CustomerMappingHistory.action == "APPROVAL_CONFLICT")) == 1

    res = _approve(db, a, "Kumar Rbr", "KUMAR POLYMERS", replace_existing=True)
    assert res["results"][0]["distributor_action"] == "updated"
    rows = list(db.scalars(select(CustomerAliasMapping).where(CustomerAliasMapping.distributor_id == a.id)
                           .order_by(CustomerAliasMapping.id)))
    assert [(m.standardized_customer_name, m.is_active, m.status) for m in rows] == [
        ("KUMAR RUBBER", False, "SUPERSEDED"), ("KUMAR POLYMERS", True, "APPROVED")]
    # The global mapping is never overwritten by a correction.
    g = db.scalar(select(CustomerAliasMapping).where(CustomerAliasMapping.distributor_id.is_(None)))
    assert g.standardized_customer_name == "KUMAR RUBBER" and res["global_conflicts"] == 1


def test_same_raw_name_maps_differently_per_distributor(db, dists):
    a, b, _c = dists
    _approve(db, a, "Sai Traders", "SAI TRADERS MUMBAI")
    _approve(db, b, "Sai Traders", "SAI TRADERS PUNE")
    m = CustomerMatcher.load(db)
    assert m.match("Sai Traders", a.id).suggested == "SAI TRADERS MUMBAI"
    assert m.match("Sai Traders", b.id).suggested == "SAI TRADERS PUNE"


def test_no_global_mapping_redirects_an_exact_master_name(db, dists):
    a, _b, c = dists
    _master(db, "Zen Polymers Ltd", "Zen Polymers Industries Ltd")
    res = _approve(db, a, "Zen Polymers Ltd", "Zen Polymers Industries Ltd")
    assert res["results"][0]["global_action"] == "skipped_master_exact"
    m = CustomerMatcher.load(db)
    assert m.match("Zen Polymers Ltd", a.id).suggested == "Zen Polymers Industries Ltd"
    assert m.match("Zen Polymers Ltd", c.id).suggested == "Zen Polymers Ltd"


def test_bulk_approval_and_validation(db, dists):
    a, _b, _c = dists
    svc = CustomerLearningService(db)
    res = svc.approve(
        [{"raw_customer": f"Cust {i}", "standard_customer": f"CUSTOMER {i}"} for i in range(25)],
        SUPER, distributor_id=a.id,
    )
    assert res["saved"] == 25
    with pytest.raises(ValidationAppError):
        svc.approve([{"raw_customer": "X", "standard_customer": "  "}], SUPER, distributor_id=a.id)
    with pytest.raises(ValidationAppError):
        svc.approve([{"raw_customer": " ", "standard_customer": "Y"}], SUPER, distributor_id=a.id)
    with pytest.raises(ValidationAppError):
        svc.approve([{"raw_customer": "X", "standard_customer": "Y"}], SUPER, distributor_id=None)
    with pytest.raises(ValidationAppError):
        svc.approve([{"raw_customer": "Dup", "standard_customer": "ONE"},
                     {"raw_customer": "DUP", "standard_customer": "TWO"}], SUPER, distributor_id=a.id)
    with pytest.raises(ValidationAppError):
        svc.approve([], SUPER, distributor_id=a.id)


def test_concurrent_conflicting_insert_raises_conflict(db, dists, monkeypatch):
    a, _b, _c = dists
    _approve(db, a, "Race Co", "RACE COMPANY")
    # Simulate a racing approval that did not see the committed mapping.
    monkeypatch.setattr(CustomerLearningService, "_active", lambda self, key, dist: [])
    with pytest.raises(ConflictError):
        with db.begin_nested():
            _approve(db, a, "Race Co", "RACE COMPANY", replace_existing=True)


def test_historical_sales_untouched_by_approval(db, dists):
    a, _b, _c = dists
    rec = _sale(db, a, "Kumar Rbr")
    before = (rec.customer_name, rec.original_customer_name, rec.customer_standardization_status, rec.updated_at)
    _approve(db, a, "Kumar Rbr", "KUMAR RUBBER")
    db.expire_all()
    rec = db.get(SalesRecord, rec.id)
    assert (rec.customer_name, rec.original_customer_name, rec.customer_standardization_status, rec.updated_at) == before
    assert rec.original_customer_name is None  # NULL = imported before customer standardization


# ------------------------------------------------------------------ ingestion
def test_apply_standardizes_future_rows_and_preserves_raw(db, dists):
    a, _b, _c = dists
    _master(db, "Shree Ganesh Rubber Industries")
    _approve(db, a, "Kumar Rbr", "KUMAR RUBBER")
    rows = [
        {"customer_name": "Kumar Rbr", "product": "P"},
        {"customer_name": "kumar rbr", "product": "Q"},
        {"customer_name": "SHREE GANESH RUBBER INDUSTRIES", "product": "P"},
        {"customer_name": "Shree Ganesh Ruber Industries", "product": "P"},
        {"customer_name": "Brand New Buyer", "product": "P"},
        {"customer_name": "", "product": "P"},
    ]
    summary = apply_customer_standardization(db, rows, distributor_id=a.id)
    assert [r["customer_name"] for r in rows] == [
        "KUMAR RUBBER", "KUMAR RUBBER", "Shree Ganesh Rubber Industries",
        "Shree Ganesh Ruber Industries", "Brand New Buyer", "",
    ]
    assert [r.get("original_customer_name") for r in rows] == [
        "Kumar Rbr", "kumar rbr", "SHREE GANESH RUBBER INDUSTRIES", "Shree Ganesh Ruber Industries",
        "Brand New Buyer", None,
    ]
    assert [r.get("customer_standardization_status") for r in rows] == [
        AUTO_MATCHED, AUTO_MATCHED, AUTO_MATCHED, NEEDS_REVIEW, UNMAPPED, None,
    ]
    assert summary == {"auto_matched": 3, "needs_review": 1, "unmapped": 1}


def test_apply_reuses_mapping_for_a_later_upload_of_another_distributor(db, dists):
    a, _b, c = dists
    _approve(db, a, "Kumar Rbr", "KUMAR RUBBER")
    rows = [{"customer": "Kumar Rbr", "product": "P"}]
    apply_customer_standardization(db, rows, distributor_id=c.id)  # global mapping
    assert rows[0]["customer_name"] == "KUMAR RUBBER" and rows[0]["customer"] == "KUMAR RUBBER"
    assert rows[0]["customer_mapping_id"] is not None


def test_incremental_identity_matches_raw_and_standardized_names():
    base = {"product": "AB820", "quantity": 5, "period": "FY 2025-26 • Q4", "source_month": "March 2026",
            "location": "", "segment": "Paper"}
    incoming = [{**base, "customer_name": "KUMAR RUBBER", "original_customer_name": "Kumar Rbr", "sales_quantity": 5}]
    # Historical row imported raw.
    plan = analyse_rows(incoming, [{**base, "id": 1, "customer_name": "Kumar Rbr"}], segment="Paper")
    assert plan["rows"][0]["kind"] == EXACT
    # Historical row already normalized by SQL (no original kept).
    plan = analyse_rows(incoming, [{**base, "id": 2, "customer_name": "KUMAR RUBBER"}], segment="Paper")
    assert plan["rows"][0]["kind"] == EXACT
    plan = analyse_rows(incoming, [{**base, "id": 3, "customer_name": "Someone Else"}], segment="Paper")
    assert plan["rows"][0]["kind"] == NEW


def test_duplicate_filter_matches_raw_or_standardized_customer(db, dists):
    from app.schemas.sales_record import ParsedSalesRow
    from app.services.erp_ingest.duplicate_service import DuplicateMixin
    from app.services.erp_ingest.validation_service import ValidationMixin

    a, _b, _c = dists
    _sale(db, a, "Kumar Rbr", product="AB820", qty="5")

    class Svc(DuplicateMixin, ValidationMixin):
        def __init__(self, session):
            self.db = session

    rows = [
        ParsedSalesRow(distributor="Alpha Dist", customer_name="KUMAR RUBBER", original_customer_name="Kumar Rbr",
                       segment="Paper", product="AB820", quantity=Decimal("5"), quantity_display="5"),
        ParsedSalesRow(distributor="Alpha Dist", customer_name="New Buyer", segment="Paper", product="AB820",
                       quantity=Decimal("5"), quantity_display="5"),
    ]
    fresh = Svc(db)._drop_existing_business_rows(a.id, "FY 2025-26 • Q4", rows)
    assert [r.customer_name for r in fresh] == ["New Buyer"]


def test_mapper_persists_customer_standardization_fields():
    from app.integrations.excel.mapper import SalesRecordMapper
    from app.schemas.sales_record import ParsedSalesRow

    row = ParsedSalesRow(
        distributor="A", customer_name="KUMAR RUBBER", segment="S", product="P", quantity=Decimal("1"),
        quantity_display="1", original_customer_name="Kumar Rbr", customer_standardization_status="AUTO_MATCHED",
        customer_mapping_id=9,
    )
    rec = SalesRecordMapper.to_orm(row, report_id=1, distributor_id=2)
    assert (rec.customer_name, rec.original_customer_name, rec.customer_standardization_status,
            rec.customer_mapping_id) == ("KUMAR RUBBER", "Kumar Rbr", "AUTO_MATCHED", 9)


# ------------------------------------------------------------------------ API
def _client(db, user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import customer_standardization as endpoint
    from app.database.session import get_db
    from app.dependencies.rbac import get_current_user
    from app.exceptions import AppException, app_exception_handler

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def email_ctx(db, dists, monkeypatch, tmp_path):
    a, _b, _c = dists
    email = EmailMessage(
        graph_message_id="g1", subject="Alpha Dist | Mumbai | Paper", sender_email="x@alpha.test",
        received_at=datetime.now(timezone.utc), subject_valid=True, parsed_distributor="Alpha Dist",
    )
    db.add(email)
    db.flush()
    atts = []
    for i in (1, 2):
        f = tmp_path / f"sales{i}.xlsx"
        f.write_bytes(b"x")
        att = EmailAttachment(graph_attachment_id=f"a{i}", file_name=f"sales{i}.xlsx", file_path=str(f),
                              email_message_id=email.id, content_hash=f"h{i}")
        db.add(att)
        atts.append(att)
    db.flush()
    db.refresh(email)
    rows_by_att = {
        atts[0].id: [
            {"customer_name": "Kumar Rubber Products Pvt Ltd", "product": "P1", "sales_quantity": 2},
            {"customer_name": "Kumar Rubber Products Pvt Ltd", "product": "P2", "sales_quantity": 3},
            {"customer_name": "Shree Ganesh Ruber Industries", "product": "P1", "sales_quantity": 1},
            {"customer_name": "Brand New Buyer", "product": "P1", "sales_quantity": 1},
            {"customer_name": "", "product": "P1", "sales_quantity": 1},
        ],
        atts[1].id: [{"customer_name": "Only In Second File", "product": "P1", "sales_quantity": 1}],
    }
    calls = {"n": 0}

    def fake_parse(self, _email, att):
        calls["n"] += 1
        return [dict(r) for r in rows_by_att[att.id]], "fake"

    from app.services.product_master import workbench_service

    monkeypatch.setattr(workbench_service.ProductWorkbenchService, "_parse_attachment", fake_parse)
    _master(db, "Kumar Rubber Products Private Limited", "Shree Ganesh Rubber Industries")
    return email, atts, a, calls


def test_workbench_metrics_and_approval_flow(db, email_ctx):
    email, atts, dist, calls = email_ctx
    client = _client(db, SUPER)
    r = client.get(f"/api/customer-standardization/emails/{email.id}/workbench")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["distributor"]["id"] == dist.id and data["attachment_id"] == atts[0].id
    assert data["metrics"] == {"total_distinct": 3, "approved": 0, "auto_matched": 1, "needs_review": 1, "unmapped": 1}
    assert data["blank_rows"] == 1
    by_raw = {i["raw_customer"]: i for i in data["items"]}
    assert by_raw["Kumar Rubber Products Pvt Ltd"]["row_count"] == 2  # repeated name grouped once
    assert by_raw["Kumar Rubber Products Pvt Ltd"]["ui_status"] == "AUTO_MATCHED"
    assert by_raw["Shree Ganesh Ruber Industries"]["suggested_customer"] == "Shree Ganesh Rubber Industries"
    assert by_raw["Brand New Buyer"]["ui_status"] == "UNMAPPED"

    bad = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "items": [{"raw_customer": "NOT IN FILE", "standard_customer": "X"}]})
    assert bad.status_code == 422
    blank = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "items": [{"raw_customer": "Brand New Buyer", "standard_customer": "   "}]})
    assert blank.status_code == 422

    ok = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "attachment_id": atts[0].id,
        "items": [{"raw_customer": "Shree Ganesh Ruber Industries", "standard_customer": "Shree Ganesh Rubber Industries"},
                  {"raw_customer": "Brand New Buyer", "standard_customer": "BRAND NEW BUYER PVT LTD"}]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["saved"] == 2

    data = client.get(f"/api/customer-standardization/emails/{email.id}/workbench").json()["data"]
    assert data["metrics"]["approved"] == 2 and data["metrics"]["needs_review"] == 0
    assert calls["n"] == 1  # snapshot reused; parser not re-run

    conflict = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "items": [{"raw_customer": "Brand New Buyer", "standard_customer": "SOMEONE ELSE"}]})
    assert conflict.status_code == 200 and len(conflict.json()["data"]["conflicts"]) == 1

    found = client.get("/api/customer-standardization/customers", params={"q": "brand"}).json()["data"]["items"]
    assert {"name": "BRAND NEW BUYER PVT LTD", "source": "approved"} in found


def test_attachment_isolation(db, email_ctx):
    email, atts, _dist, _calls = email_ctx
    client = _client(db, SUPER)
    data = client.get(f"/api/customer-standardization/emails/{email.id}/workbench",
                      params={"attachment_id": atts[1].id}).json()["data"]
    assert [i["raw_customer"] for i in data["items"]] == ["Only In Second File"]
    r = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "attachment_id": atts[1].id,
        "items": [{"raw_customer": "Brand New Buyer", "standard_customer": "BRAND NEW BUYER"}]})
    assert r.status_code == 422
    assert db.scalar(select(func.count()).select_from(CustomerAliasMapping)) == 0


def test_workbench_blocks_non_owner_user(db, email_ctx):
    email, _atts, _dist, _calls = email_ctx
    user = RequestUser(role=UserRole.USER, name="u", user_id=None, email="other@x.test")
    client = _client(db, user)
    assert client.get(f"/api/customer-standardization/emails/{email.id}/workbench").status_code == 403
    r = client.post("/api/customer-standardization/approve", json={
        "email_id": email.id, "items": [{"raw_customer": "Brand New Buyer", "standard_customer": "X"}]})
    assert r.status_code == 403
    assert db.scalar(select(func.count()).select_from(CustomerAliasMapping)) == 0


def test_endpoints_require_authentication(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import customer_standardization as endpoint
    from app.database.session import get_db
    from app.exceptions import AppException, app_exception_handler

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    assert client.get("/api/customer-standardization/customers").status_code in (401, 403)
    body = {"email_id": 1, "items": [{"raw_customer": "A", "standard_customer": "B"}]}
    assert client.post("/api/customer-standardization/approve", json=body).status_code in (401, 403)
    assert db.scalar(select(func.count()).select_from(CustomerAliasMapping)) == 0
