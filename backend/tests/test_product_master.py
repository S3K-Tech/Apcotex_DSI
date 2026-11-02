"""FG Master upload, matching priority, approval/learning, and historical-data protection."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.dependencies.rbac import RequestUser
from app.enums import UserRole
from app.exceptions import ConflictError, ValidationAppError
from app.models.distributor import Distributor
from app.models.email_message import EmailAttachment, EmailMessage
from app.models.fg_master import FGMasterProduct, FGMasterVersion
from app.models.product_alias_mapping import ProductAliasMapping
from app.models.product_mapping_history import ProductMappingHistory
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.services.incremental_upload import EXACT, analyse_rows
from app.services.product_master.apply import apply_product_standardization
from app.services.product_master.learning_service import ProductLearningService
from app.services.product_master.master_service import (
    FGMasterService,
    parse_master_workbook,
    validate_master_file,
)
from app.services.product_master.matching import (
    AUTO_MATCHED,
    METHOD_DISTRIBUTOR_MAPPING,
    METHOD_GLOBAL_MAPPING,
    METHOD_MASTER_EXACT,
    NEEDS_REVIEW,
    UNMAPPED,
    ProductMatcher,
    similarity_score,
)
from app.services.product_master.normalize import normalize_key

SUPER = RequestUser(role=UserRole.SUPER_ADMIN, name="super", user_id=None)


def _xlsx(rows, headers=("Material", "Plant", "Material Description", "Valuation Type")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "FG"
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _master_rows(descriptions):
    return [(f"M{i}", "P1", d, "VT") for i, d in enumerate(descriptions)]


def _publish(db, descriptions, name="fg.xlsx"):
    svc = FGMasterService(db)
    out = svc.upload_draft(name, _xlsx(_master_rows(descriptions)), SUPER)
    svc.publish(out["version"]["id"], SUPER)
    return out


@pytest.fixture()
def dists(db):
    a = Distributor(name="Alpha Dist", company="Alpha Dist")
    b = Distributor(name="Beta Dist", company="Beta Dist")
    c = Distributor(name="Gamma Dist", company="Gamma Dist")
    db.add_all([a, b, c])
    db.flush()
    return a, b, c


# ------------------------------------------------------------------ normalize
def test_normalize_key_is_case_space_and_punctuation_insensitive():
    assert normalize_key("  apcotex  ab-820 ") == "APCOTEX AB 820"
    assert normalize_key("AB_820.") == "AB 820"
    assert normalize_key(None) == ""


# --------------------------------------------------------------- master parse
def test_parse_uses_only_material_description_and_counts():
    data = _xlsx(
        [
            ("100", "P1", "APCOTEX AB 820", "VT"),
            ("101", "P2", "  apcotex  ab-820 ", "VT"),  # duplicate after normalization
            ("102", "P1", "APCOFLEX N 385", "VT"),
            ("103", "P1", None, "VT"),  # invalid: empty description
            ("104", "P1", "12345", "VT"),  # invalid: no letters
            (None, None, None, None),  # fully blank row is ignored
        ]
    )
    parsed = parse_master_workbook(data)
    assert [d for d, _k in parsed.valid] == ["APCOTEX AB 820", "APCOFLEX N 385"]
    assert len(parsed.duplicates) == 1 and parsed.duplicates[0]["first_row"] == 2
    assert {i["reason"] for i in parsed.invalid} == {"Material Description is empty", "No alphabetic characters"}
    assert parsed.total_rows == 5
    # Material codes / plants never become descriptions
    assert all(not d.startswith("M") or d.startswith("MATERIAL") for d, _ in parsed.valid)


def test_parse_rejects_missing_column():
    data = _xlsx([("1", "P1", "X", "VT")], headers=("Material", "Plant", "Description", "Valuation Type"))
    with pytest.raises(ValidationAppError, match="Material Description"):
        parse_master_workbook(data)


def test_parse_rejects_ambiguous_column():
    data = _xlsx([("A", "B")], headers=("Material Description", "material description"))
    with pytest.raises(ValidationAppError, match="more than one"):
        parse_master_workbook(data)


def test_file_validation_type_size_structure():
    with pytest.raises(ValidationAppError):
        validate_master_file("fg.csv", b"a,b")
    with pytest.raises(ValidationAppError):
        validate_master_file("fg.xlsx", b"not a zip")
    with pytest.raises(ValidationAppError):
        validate_master_file("fg.xlsx", b"")
    with pytest.raises(ValidationAppError, match="limit"):
        validate_master_file("fg.xlsx", b"PK" + b"0" * (10 * 1024 * 1024 + 1))


# --------------------------------------------------------- versioning / diff
def test_upload_publish_replace_diff_and_single_active(db, dists):
    svc = FGMasterService(db)
    assert svc.status()["configured"] is False
    _publish(db, ["APCOTEX AB 820", "APCOFLEX N 385", "OLD GRADE X"])
    active = svc.active_version()
    assert active.version_number == 1 and active.status == "active"

    db.add(
        ProductAliasMapping(
            distributor_id=None, original_product_name="oldx", normalized_original_name="OLDX",
            normalized_product_name="OLD GRADE X", scope="GLOBAL", status="APPROVED", is_active=True,
        )
    )
    db.flush()
    draft = svc.upload_draft("fg2.xlsx", _xlsx(_master_rows(["APCOTEX AB 820", "APCOFLEX N 385", "NEW GRADE Y"])), SUPER)
    diff = draft["diff"]
    assert diff["new_count"] == 1 and diff["new_sample"] == ["NEW GRADE Y"]
    assert diff["removed_count"] == 1 and diff["removed_sample"] == ["OLD GRADE X"]
    assert diff["unchanged_count"] == 2
    assert diff["affected_mappings_count"] == 1
    # Draft is not live until published
    assert svc.active_version().version_number == 1
    assert svc.status()["draft"]["version_number"] == 2

    svc.publish(draft["version"]["id"], SUPER)
    statuses = dict(db.execute(select(FGMasterVersion.version_number, FGMasterVersion.status)).all())
    assert statuses == {1: "superseded", 2: "active"}
    with pytest.raises(ConflictError):
        svc.publish(draft["version"]["id"], SUPER)


def test_new_upload_discards_previous_draft(db):
    svc = FGMasterService(db)
    first = svc.upload_draft("a.xlsx", _xlsx(_master_rows(["A ONE"])), SUPER)
    svc.upload_draft("b.xlsx", _xlsx(_master_rows(["B TWO"])), SUPER)
    assert db.get(FGMasterVersion, first["version"]["id"]).status == "discarded"


def test_search_products_autocomplete(db):
    _publish(db, ["APCOTEX AB 820", "APCOFLEX N 385"])
    res = FGMasterService(db).search_products("ab 8")
    assert res["configured"] and res["items"] == ["APCOTEX AB 820"]


# ------------------------------------------------------------------ matching
def test_matching_priority_order(db, dists):
    a, b, _c = dists
    _publish(db, ["APCOTEX AB 820", "APCOTEX AB 850", "APCOFLEX N 385"])
    db.add_all(
        [
            ProductAliasMapping(
                distributor_id=None, original_product_name="AB820", normalized_original_name="AB820",
                normalized_product_name="APCOTEX AB 820", scope="GLOBAL", status="APPROVED", is_active=True,
            ),
            ProductAliasMapping(
                distributor_id=b.id, original_product_name="AB820", normalized_original_name="AB820",
                normalized_product_name="APCOTEX AB 850", scope="DISTRIBUTOR", status="APPROVED", is_active=True,
            ),
        ]
    )
    db.flush()
    m = ProductMatcher.load(db)
    r_b = m.match("ab820", b.id)
    assert (r_b.status, r_b.method, r_b.suggested) == (AUTO_MATCHED, METHOD_DISTRIBUTOR_MAPPING, "APCOTEX AB 850")
    r_a = m.match("AB820", a.id)
    assert (r_a.method, r_a.suggested) == (METHOD_GLOBAL_MAPPING, "APCOTEX AB 820")
    r_exact = m.match("apcoflex n-385", a.id)
    assert (r_exact.status, r_exact.method, r_exact.suggested) == (AUTO_MATCHED, METHOD_MASTER_EXACT, "APCOFLEX N 385")
    r_fuzzy = m.match("APCOFLX N385", a.id)
    assert r_fuzzy.status == NEEDS_REVIEW and r_fuzzy.suggested == "APCOFLEX N 385"
    assert r_fuzzy.approved is False
    r_none = m.match("TOTALLY DIFFERENT THING", a.id)
    assert r_none.status == UNMAPPED and r_none.suggested is None


def test_similarity_never_confuses_grade_numbers():
    assert similarity_score("AB 820", "APCOTEX AB 850") < 75
    assert similarity_score("AB 820", "APCOTEX AB 820") >= 75


def test_matching_without_master_keeps_raw_and_uses_mappings(db, dists):
    a, _b, _c = dists
    db.add(
        ProductAliasMapping(
            distributor_id=None, original_product_name="N385", normalized_original_name="N385",
            normalized_product_name="APCOFLEX N 385", scope="GLOBAL", status="APPROVED", is_active=True,
        )
    )
    db.flush()
    m = ProductMatcher.load(db)
    assert m.master_configured is False
    assert m.match("N385", a.id).suggested == "APCOFLEX N 385"
    assert m.match("SOMETHING", a.id).status == UNMAPPED


# --------------------------------------------------------- approval/learning
def test_learning_global_and_distributor_conflicts(db, dists):
    a, b, c = dists
    _publish(db, ["APCOTEX AB 820", "APCOTEX AB 850"])
    learn = ProductLearningService(db)

    r1 = learn.approve([{"raw_product": "AB820", "standard_product": "apcotex ab 820"}], SUPER, distributor_id=a.id)
    assert r1["results"][0]["distributor_action"] == "created"
    assert r1["results"][0]["global_action"] == "created"
    assert r1["results"][0]["standard_product"] == "APCOTEX AB 820"  # canonical master spelling

    r2 = learn.approve([{"raw_product": "AB820", "standard_product": "APCOTEX AB 850"}], SUPER, distributor_id=b.id)
    assert r2["results"][0]["global_action"] == "conflict_kept_distributor"
    assert r2["global_conflicts"] == 1

    m = ProductMatcher.load(db)
    assert m.match("AB820", a.id).suggested == "APCOTEX AB 820"
    assert m.match("AB820", b.id).suggested == "APCOTEX AB 850"
    assert m.match("AB820", c.id).suggested == "APCOTEX AB 820"  # learned global

    actions = list(db.scalars(select(ProductMappingHistory.action).order_by(ProductMappingHistory.id)))
    assert actions == ["CREATED", "CREATED", "CREATED", "GLOBAL_CONFLICT"]


def test_same_distributor_correction_supersedes_own_mapping_only(db, dists):
    a, _b, _c = dists
    _publish(db, ["APCOTEX AB 820", "APCOTEX AB 850"])
    learn = ProductLearningService(db)
    learn.approve([{"raw_product": "AB820", "standard_product": "APCOTEX AB 820"}], SUPER, distributor_id=a.id)
    r = learn.approve([{"raw_product": "ab-820", "standard_product": "APCOTEX AB 850"}], SUPER, distributor_id=a.id)
    assert r["results"][0]["distributor_action"] == "updated"
    active = list(
        db.scalars(
            select(ProductAliasMapping).where(
                ProductAliasMapping.distributor_id == a.id, ProductAliasMapping.is_active.is_(True)
            )
        )
    )
    assert len(active) == 1 and active[0].normalized_product_name == "APCOTEX AB 850"
    glob = db.scalar(select(ProductAliasMapping).where(ProductAliasMapping.distributor_id.is_(None)))
    assert glob.normalized_product_name == "APCOTEX AB 820"  # global never overwritten


def test_approval_validation_and_custom_values(db, dists):
    a, _b, _c = dists
    _publish(db, ["APCOTEX AB 820"])
    learn = ProductLearningService(db)
    with pytest.raises(ValidationAppError):
        learn.approve([{"raw_product": "X1", "standard_product": "   "}], SUPER, distributor_id=a.id)
    with pytest.raises(ValidationAppError):
        learn.approve([{"raw_product": "X1", "standard_product": "Y"}], SUPER, distributor_id=None)
    with pytest.raises(ValidationAppError):
        learn.approve(
            [{"raw_product": "X1", "standard_product": "A"}, {"raw_product": "x-1", "standard_product": "B"}],
            SUPER, distributor_id=a.id,
        )
    learn.approve([{"raw_product": "Special Blend", "standard_product": "CUSTOM BLEND Z"}], SUPER, distributor_id=a.id)
    mapping = db.scalar(select(ProductAliasMapping).where(ProductAliasMapping.distributor_id == a.id))
    assert mapping.is_custom_value is True
    # Custom value is a mapping, not a master entry
    assert db.scalar(
        select(func.count()).select_from(FGMasterProduct).where(FGMasterProduct.material_description == "CUSTOM BLEND Z")
    ) == 0


def test_no_global_mapping_redirects_exact_master_description(db, dists):
    a, _b, c = dists
    _publish(db, ["APCOTEX AB 820", "APCOTEX AB 850"])
    r = ProductLearningService(db).approve(
        [{"raw_product": "APCOTEX AB 820", "standard_product": "APCOTEX AB 850"}], SUPER, distributor_id=a.id
    )
    assert r["results"][0]["global_action"] == "skipped_master_exact"
    m = ProductMatcher.load(db)
    assert m.match("APCOTEX AB 820", a.id).suggested == "APCOTEX AB 850"
    assert m.match("APCOTEX AB 820", c.id).suggested == "APCOTEX AB 820"


# ----------------------------------------------- future-only / historical data
def _historical(db, dist):
    report = Report(name="hist", reporting_month="FY 2025-26 • Q4", distributor_id=dist.id, status="completed")
    db.add(report)
    db.flush()
    rec = SalesRecord(
        customer_name="Acme", segment="Paper", product="AB820", quantity=Decimal("5"), period="FY 2025-26 • Q4",
        source_month="March 2026", location="", unit="MT", row_hash="h1", report_id=report.id, distributor_id=dist.id,
    )
    db.add(rec)
    db.flush()
    return rec


def test_historical_sales_untouched_by_publish_and_approval(db, dists):
    a, _b, _c = dists
    rec = _historical(db, a)
    before = (rec.product, rec.original_product_name, rec.product_standardization_status, rec.updated_at)
    _publish(db, ["APCOTEX AB 820"])
    ProductLearningService(db).approve([{"raw_product": "AB820", "standard_product": "APCOTEX AB 820"}], SUPER, distributor_id=a.id)
    db.expire_all()
    rec = db.get(SalesRecord, rec.id)
    assert (rec.product, rec.original_product_name, rec.product_standardization_status, rec.updated_at) == before
    assert rec.original_product_name is None  # NULL = imported before standardization


def test_apply_standardization_on_new_rows_only(db, dists):
    a, _b, _c = dists
    _publish(db, ["APCOTEX AB 820", "APCOFLEX N 385"])
    ProductLearningService(db).approve([{"raw_product": "AB820", "standard_product": "APCOTEX AB 820"}], SUPER, distributor_id=a.id)
    rows = [
        {"product": "AB820", "customer_name": "Acme"},
        {"product": "apcoflex n-385", "customer_name": "Acme"},
        {"product": "APCOFLX N385", "customer_name": "Acme"},
        {"product": "Mystery", "customer_name": "Acme"},
    ]
    summary = apply_product_standardization(db, rows, distributor_id=a.id)
    assert [r["product"] for r in rows] == ["APCOTEX AB 820", "APCOFLEX N 385", "APCOFLX N385", "Mystery"]
    assert [r["original_product_name"] for r in rows] == ["AB820", "apcoflex n-385", "APCOFLX N385", "Mystery"]
    assert [r["product_standardization_status"] for r in rows] == [AUTO_MATCHED, AUTO_MATCHED, NEEDS_REVIEW, UNMAPPED]
    assert summary == {"auto_matched": 2, "needs_review": 1, "unmapped": 1, "master_configured": True}


def test_apply_without_master_preserves_raw(db, dists):
    a, _b, _c = dists
    rows = [{"product": "AB820"}]
    summary = apply_product_standardization(db, rows, distributor_id=a.id)
    assert rows[0]["product"] == "AB820" and rows[0]["product_standardization_status"] == UNMAPPED
    assert summary["master_configured"] is False


def test_incremental_identity_matches_historical_raw_names():
    existing = [{"id": 1, "customer_name": "Acme", "product": "AB820", "quantity": 5, "period": "FY 2025-26 • Q4",
                 "source_month": "March 2026", "location": "", "segment": "Paper"}]
    incoming = [{"customer_name": "Acme", "product": "APCOTEX AB 820", "original_product_name": "AB820",
                 "sales_quantity": 5, "period": "FY 2025-26 • Q4", "source_month": "March 2026", "segment": "Paper"}]
    plan = analyse_rows(incoming, existing, segment="Paper")
    assert plan["rows"][0]["kind"] == EXACT


def test_mapper_persists_standardization_fields():
    from app.integrations.excel.mapper import SalesRecordMapper
    from app.schemas.sales_record import ParsedSalesRow

    row = ParsedSalesRow(
        distributor="A", customer_name="C", segment="S", product="APCOTEX AB 820", quantity=Decimal("1"),
        quantity_display="1", original_product_name="AB820", product_standardization_status="AUTO_MATCHED",
        product_mapping_id=7,
    )
    rec = SalesRecordMapper.to_orm(row, report_id=1, distributor_id=2)
    assert (rec.original_product_name, rec.product_standardization_status, rec.product_mapping_id) == (
        "AB820", "AUTO_MATCHED", 7,
    )


# ---------------------------------------------------------------- endpoints
def _client(db, user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import product_standardization as endpoint
    from app.database.session import get_db
    from app.dependencies.rbac import get_current_user
    from app.exceptions import AppException, app_exception_handler

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "user"])
def test_master_endpoints_require_super_admin(db, role):
    client = _client(db, RequestUser(role=UserRole(role), name="x", user_id=None))
    assert client.get("/api/product-standardization/master/status").status_code == 403
    files = {"file": ("fg.xlsx", _xlsx(_master_rows(["A ONE"])), "application/octet-stream")}
    assert client.post("/api/product-standardization/master/upload", files=files).status_code == 403
    assert db.scalar(select(func.count()).select_from(FGMasterVersion)) == 0


def test_master_upload_and_publish_endpoint(db):
    client = _client(db, SUPER)
    files = {"file": ("fg.xlsx", _xlsx(_master_rows(["A ONE", "B TWO"])), "application/octet-stream")}
    r = client.post("/api/product-standardization/master/upload", files=files)
    assert r.status_code == 200, r.text
    vid = r.json()["data"]["version"]["id"]
    assert client.post(f"/api/product-standardization/master/versions/{vid}/publish", json={"confirm": False}).status_code == 422
    r = client.post(f"/api/product-standardization/master/versions/{vid}/publish", json={"confirm": True})
    assert r.status_code == 200 and r.json()["data"]["version"]["status"] == "active"
    bad = {"file": ("fg.csv", b"a,b", "text/csv")}
    assert client.post("/api/product-standardization/master/upload", files=bad).status_code == 422


@pytest.fixture()
def email_ctx(db, dists, monkeypatch, tmp_path):
    a, _b, _c = dists
    email = EmailMessage(
        graph_message_id="g1", subject="Alpha Dist | Mumbai | Paper", sender_email="x@alpha.test",
        received_at=datetime.now(timezone.utc), subject_valid=True, parsed_distributor="Alpha Dist",
    )
    db.add(email)
    db.flush()
    f = tmp_path / "sales.xlsx"
    f.write_bytes(b"x")
    att = EmailAttachment(graph_attachment_id="a1", file_name="sales.xlsx", file_path=str(f),
                          email_message_id=email.id, content_hash="h1")
    db.add(att)
    db.flush()
    db.refresh(email)
    rows = [
        {"customer_name": "Acme", "product": "apcoflex n-385", "sales_quantity": 2},
        {"customer_name": "Zen", "product": "APCOFLX N385", "sales_quantity": 3},
        {"customer_name": "Zen", "product": "Mystery", "sales_quantity": 1},
    ]
    calls = {"n": 0}

    def fake_parse(self, _email, _att):
        calls["n"] += 1
        return [dict(r) for r in rows], "fake"

    from app.services.product_master import workbench_service

    monkeypatch.setattr(workbench_service.ProductWorkbenchService, "_parse_attachment", fake_parse)
    _publish(db, ["APCOFLEX N 385"])
    return email, att, a, calls


def test_workbench_metrics_preview_and_approve(db, email_ctx):
    email, att, dist, calls = email_ctx
    client = _client(db, SUPER)
    r = client.get(f"/api/product-standardization/emails/{email.id}/workbench")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["distributor"]["id"] == dist.id and data["attachment_id"] == att.id
    assert data["metrics"] == {"total_distinct": 3, "auto_standardized": 1, "awaiting_review": 1, "unmapped": 1, "approved": 0}

    prev = client.get(f"/api/product-standardization/emails/{email.id}/preview").json()["data"]
    by_raw = {row["original_product"]: row for row in prev["rows"]}
    assert by_raw["APCOFLX N385"]["standardized_product"] is None  # suggestion is not an approval
    assert by_raw["APCOFLX N385"]["suggested_product"] == "APCOFLEX N 385"
    assert by_raw["apcoflex n-385"]["standardized_product"] == "APCOFLEX N 385"

    bad = client.post("/api/product-standardization/approve", json={
        "email_id": email.id, "items": [{"raw_product": "NOT IN FILE", "standard_product": "APCOFLEX N 385"}]})
    assert bad.status_code == 422
    ok = client.post("/api/product-standardization/approve", json={
        "email_id": email.id, "attachment_id": att.id,
        "items": [{"raw_product": "APCOFLX N385", "standard_product": "APCOFLEX N 385"},
                  {"raw_product": "Mystery", "standard_product": "Custom Mystery Grade"}]})
    assert ok.status_code == 200, ok.text

    data = client.get(f"/api/product-standardization/emails/{email.id}/workbench").json()["data"]
    assert data["metrics"]["approved"] == 2 and data["metrics"]["awaiting_review"] == 0
    assert calls["n"] == 1  # snapshot reused; parser not re-run


def test_workbench_blocks_non_owner_user(db, email_ctx):
    email, _att, _dist, _calls = email_ctx
    user = RequestUser(role=UserRole.USER, name="u", user_id=None, email="other@x.test")
    client = _client(db, user)
    assert client.get(f"/api/product-standardization/emails/{email.id}/workbench").status_code == 403
