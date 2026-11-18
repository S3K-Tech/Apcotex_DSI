"""Quarterly executive report: filters, grounded analytics, LLM summary, PDF, recipients, email."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.dependencies.rbac import RequestUser
from app.enums import UserRole
from app.llm.bedrock_client import BedrockCompletion
from app.models.audit_trail import AuditTrail
from app.models.distributor import Distributor
from app.models.executive_report import ExecutiveReport, ExecutiveReportEmail
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.models.user import User
from app.services.executive_report.analytics import build_snapshot
from app.services.executive_report.filters import (
    comparison_periods,
    filter_key,
    normalize_filters,
    period_label,
)
from app.services.executive_report.summary import (
    SECTION_KEYS,
    UNVERIFIED_PLACEHOLDER,
    ExecutiveSummaryError,
    build_summary,
    llm_payload,
)
from app.services.sales_insights_service import SalesInsightsService

SUPER = RequestUser(role=UserRole.SUPER_ADMIN, name="super", user_id=None)
Q1 = {"fiscal_year_start": 2026, "period": "q1"}


# ------------------------------------------------------------------ fixtures
@pytest.fixture()
def sales(db):
    """Q1 FY 2026-27 (20 MT) and the previous quarter Q4 FY 2025-26 (10 MT)."""
    alpha = Distributor(name="Alpha Dist", company="Alpha Dist")
    beta = Distributor(name="Beta Dist", company="Beta Dist")
    db.add_all([alpha, beta])
    db.flush()
    reports = {}

    def sale(dist, customer, product, qty, period, month, segment="Paper"):
        key = (dist.id, period)
        if key not in reports:
            reports[key] = Report(name=f"{dist.name} {period}", reporting_month=period,
                                  distributor_id=dist.id, status="completed")
            db.add(reports[key])
            db.flush()
        db.add(SalesRecord(
            customer_name=customer, segment=segment, product=product, quantity=Decimal(qty),
            period=period, source_month=month, location="Mumbai", unit="MT",
            row_hash=f"{dist.id}-{customer}-{product}-{period}-{month}",
            report_id=reports[key].id, distributor_id=dist.id,
        ))

    q1, q4 = "FY 2026-27 • Q1", "FY 2025-26 • Q4"
    sale(alpha, "Kumar Rubber", "P1", "10", q1, "April 2026")
    sale(alpha, "Kumar Rubber", "P2", "5", q1, "May 2026")
    sale(alpha, "Shree Ganesh", "P1", "2", q1, "June 2026")
    sale(beta, "Carpet Co", "P3", "3", q1, "April 2026", segment="Carpet")
    sale(alpha, "Kumar Rubber", "P1", "4", q4, "March 2026")
    sale(alpha, "Old Buyer", "P4", "6", q4, "March 2026")
    db.flush()
    return alpha, beta


def _completion(text: str) -> BedrockCompletion:
    return BedrockCompletion(text=text, model_id="fake-model", region="", input_tokens=100,
                             output_tokens=200, latency_ms=5)


def grounded_reply(payload: dict) -> str:
    """A well-behaved LLM reply that only uses figures present in ``payload``."""
    kpis = payload["kpis"]
    top = payload["products"]["highest"][0]
    cust = payload["customers"]["highest"][0]
    sections = []
    for key in SECTION_KEYS:
        sections.append({"key": key, "paragraphs": [f"Section {key} for {payload['reporting_period']}."],
                         "bullets": []})
    sections[0]["paragraphs"] = [
        f"Total sales were {kpis['total_sales_quantity']} across {kpis['customers_served']} customers."
    ]
    sections[2]["bullets"] = [f"{top['name']} led with {top['mt_display']} MT ({top['share_pct']}% share)."]
    sections[3]["bullets"] = [f"{cust['name']} was the top customer with {cust['mt_display']} MT."]
    return json.dumps({"sections": sections})


class FakeCompleter:
    def __init__(self, reply=grounded_reply, error: Exception | None = None):
        self.calls = []
        self.reply = reply
        self.error = error

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _completion(self.reply(json.loads(kwargs["user"])))


class FakeGraph:
    def __init__(self, error: Exception | None = None):
        self.sent = []
        self.error = error

    def send_mail(self, **kwargs):
        if self.error:
            raise self.error
        self.sent.append(kwargs)


def _client(db, user, *, completer=None, graph=None, monkeypatch=None, segments=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import executive_reports as endpoint
    from app.database.session import get_db
    from app.dependencies.rbac import get_current_user
    from app.exceptions import AppException, app_exception_handler
    from app.services.executive_report.service import ExecutiveReportService

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[endpoint.get_report_service] = lambda: ExecutiveReportService(
        db, completer=completer or FakeCompleter()
    )
    if monkeypatch is not None:
        monkeypatch.setattr(endpoint, "segment_scope_for_user", lambda *_: segments)
        monkeypatch.setattr(endpoint, "distributor_companies_scope_for_user", lambda *_: None)
        if graph is not None:
            from app.integrations.graph import client as graph_module

            monkeypatch.setattr(graph_module, "GraphClient", lambda: graph)
    return TestClient(app)


def _generate(client, filters=Q1):
    r = client.post("/api/executive-reports/generate", json={"filters": filters})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ------------------------------------------------------------------ filters
def test_filters_normalize_and_identify_the_selection():
    from app.services.executive_report.filters import report_filename_stem, report_title

    f = normalize_filters({"period": "Q1", "fiscal_year_start": 2026, "distributor": "All", "segment": " Paper "})
    assert f["distributor"] is None and f["segment"] == "Paper" and f["period"] == "q1"
    assert normalize_filters({"period": "last_3_months", "fiscal_year_start": 2026})["fiscal_year_start"] is None
    assert normalize_filters({"period": "q1", "start_month": "2026-04"})["start_month"] is None
    assert filter_key({**Q1, "segment": "paper"}) == filter_key({**Q1, "segment": "Paper"})
    assert filter_key(Q1) != filter_key({**Q1, "period": "q2"})
    assert filter_key(Q1) != filter_key({**Q1, "distributor": "Alpha Dist"})
    assert period_label(normalize_filters(Q1)) == "Q1 FY 2026-27"
    assert period_label(normalize_filters({"period": "full_year", "fiscal_year_start": 2026})) == "FY 2026-27"
    assert report_title(normalize_filters(Q1)) == "Quarterly Secondary Sales Report"
    assert report_title(normalize_filters({"period": "full_year", "fiscal_year_start": 2026})) == "Annual Secondary Sales Report"
    assert "Annual_Secondary_Sales" in report_filename_stem(
        normalize_filters({"period": "full_year", "fiscal_year_start": 2026})
    )


def test_comparison_periods():
    q1 = comparison_periods(normalize_filters(Q1))
    assert [c["label"] for c in q1] == ["Q4 FY 2025-26", "Q1 FY 2025-26"]
    q3 = comparison_periods(normalize_filters({"period": "q3", "fiscal_year_start": 2026}))
    assert q3[0]["label"] == "Q2 FY 2026-27"
    fy = comparison_periods(normalize_filters({"period": "full_year", "fiscal_year_start": 2026}))
    assert [c["label"] for c in fy] == ["FY 2025-26"]
    assert comparison_periods(normalize_filters({"period": "last_6_months"})) == []


# ------------------------------------------------------------------ analytics
def test_snapshot_is_deterministic_and_matches_dashboard(db, sales):
    filters = normalize_filters(Q1)
    snap = build_snapshot(db, filters, allowed_segments=None, allowed_companies=None)
    dash = SalesInsightsService(db).sales_insights(**filters)
    assert snap["kpis"]["total_sales_mt"] == dash["kpis"]["total_sales_mt"] == 20.0
    assert snap["kpis"]["total_customers"] == 3 and snap["kpis"]["active_distributors"] == 2
    assert [(p["name"], p["mt"], p["share_pct"]) for p in snap["products"]] == [
        ("P1", 12.0, 60.0), ("P2", 5.0, 25.0), ("P3", 3.0, 15.0)]
    assert snap["customers"][0]["name"] == "Kumar Rubber" and snap["customers"][0]["mt"] == 15.0
    assert snap["concentration"]["customers"]["top1_share_pct"] == 75.0
    assert snap["charts"]["monthly_trend"] == dash["monthly_trend"]
    assert snap["monthly_quality"]["monthly_basis"] == "recorded_months"

    prev, last_year = snap["comparisons"]
    assert prev["available"] and prev["total_mt"] == 10.0 and prev["change_mt"] == 10.0 and prev["change_pct"] == 100.0
    assert [m["name"] for m in prev["products"]["no_sales_in_current_period"]] == ["P4"]
    assert {m["name"] for m in prev["products"]["only_in_current_period"]} == {"P2", "P3"}
    assert prev["products"]["increases"][0] == {
        "name": "P1", "current_mt": 12.0, "previous_mt": 4.0, "change_mt": 8.0, "change_pct": 200.0}
    assert last_year["available"] is False and "total_mt" not in last_year


def test_snapshot_respects_segment_scope_and_distributor_filter(db, sales):
    scoped = build_snapshot(db, normalize_filters(Q1), allowed_segments=["Paper"], allowed_companies=None)
    assert scoped["kpis"]["total_sales_mt"] == 17.0
    names = json.dumps(llm_payload(scoped))
    assert "Carpet Co" not in names and "Beta Dist" not in names

    beta = build_snapshot(db, normalize_filters({**Q1, "distributor": "Beta Dist"}),
                          allowed_segments=None, allowed_companies=None)
    assert beta["kpis"]["total_sales_mt"] == 3.0 and [d["name"] for d in beta["distributors"]] == ["Beta Dist"]


# ------------------------------------------------------------------ summary verification
def test_verifier_drops_statements_with_invented_figures(db, sales):
    payload = llm_payload(build_snapshot(db, normalize_filters(Q1), allowed_segments=None, allowed_companies=None))
    reply = json.dumps({"sections": [
        {"key": "executive_overview",
         "paragraphs": ["Total sales were 20.000 MT. Sales grew 37.5% on last year. Kumar Rubber led."],
         "bullets": ["P1 contributed 60.0% of volume.", "Revenue reached 4,500,000."]},
        {"key": "sales_performance", "paragraphs": ["Versus Q4 FY 2025-26, sales rose by 10.000 MT (100.0%)."]},
        {"key": "product_performance", "paragraphs": ["P9 sold 77.7 MT."]},
        *[{"key": k, "paragraphs": ["Observation."]} for k in SECTION_KEYS[3:]],
    ]})
    summary = build_summary(reply, payload)
    sections = {s["key"]: s for s in summary["sections"]}
    assert sections["executive_overview"]["paragraphs"] == ["Total sales were 20.000 MT. Kumar Rubber led."]
    assert sections["executive_overview"]["bullets"] == ["P1 contributed 60.0% of volume."]
    assert "100.0%" in sections["sales_performance"]["paragraphs"][0]
    assert sections["product_performance"]["paragraphs"] == [UNVERIFIED_PLACEHOLDER]
    assert summary["verification"]["removed_statements"] == 3
    assert [s["key"] for s in summary["sections"]] == list(SECTION_KEYS)
    # Structure caps keep the brief board-pack size even if the model overwrites.
    assert all(len(s["paragraphs"]) <= 1 for s in summary["sections"])
    assert all(len(s["bullets"]) <= 6 for s in summary["sections"])


def test_unreadable_or_incomplete_llm_output_is_rejected():
    with pytest.raises(ExecutiveSummaryError):
        build_summary("I cannot help with that.", {})
    with pytest.raises(ExecutiveSummaryError):
        build_summary(json.dumps({"sections": [{"key": "executive_overview", "paragraphs": ["x"]}]}), {})


# ------------------------------------------------------------------ generate API
def test_generate_uses_selected_filters_and_grounded_payload(db, sales):
    completer = FakeCompleter()
    client = _client(db, SUPER, completer=completer)
    data = _generate(client, {**Q1, "distributor": "All", "segment": "All"})

    assert data["period_label"] == "Q1 FY 2026-27"
    assert data["filter_key"] == filter_key(Q1)
    assert [s["key"] for s in data["summary"]["sections"]] == list(SECTION_KEYS)
    assert data["summary"]["verification"]["removed_statements"] == 0
    assert data["kpis"]["total_sales_mt"] == 20.0
    assert data["comparisons"][1]["available"] is False

    call = completer.calls[0]
    assert call["max_tokens"] >= 3000 and call["reason"] == "executive_summary"
    assert "Never invent sales quantities" in call["system"]
    assert "Negative Observations and Business Risks section is mandatory" in call["system"]
    assert "450 and 750 words" in call["system"]
    assert "bullet_first_no_essay" in call["user"]
    sent = json.loads(call["user"])
    assert sent["reporting_period"] == "Q1 FY 2026-27"
    assert sent["kpis"]["total_sales_quantity"] == "20.000 MT"

    report = db.get(ExecutiveReport, data["id"])
    assert report.analytics["kpis"]["total_sales_mt"] == 20.0 and report.model == "fake-model"
    assert db.scalar(select(func.count()).select_from(AuditTrail).where(
        AuditTrail.entity_type == "executive_report")) == 1


def test_generate_never_sends_unauthorised_segments_to_the_llm(db, sales, monkeypatch):
    completer = FakeCompleter()
    client = _client(db, SUPER, completer=completer, monkeypatch=monkeypatch, segments=["Paper"])
    data = _generate(client)
    assert data["kpis"]["total_sales_mt"] == 17.0
    assert "Carpet Co" not in completer.calls[0]["user"]


def test_generate_without_data_does_not_call_the_llm(db, sales):
    completer = FakeCompleter()
    client = _client(db, SUPER, completer=completer)
    r = client.post("/api/executive-reports/generate", json={"filters": {"fiscal_year_start": 2024, "period": "q2"}})
    assert r.status_code == 422 and completer.calls == []


def test_llm_failure_is_reported_and_nothing_is_saved(db, sales):
    from app.llm.provider_clients import LlmProviderError

    client = _client(db, SUPER, completer=FakeCompleter(error=LlmProviderError("OpenAI rate limit or quota exceeded.")))
    r = client.post("/api/executive-reports/generate", json={"filters": Q1})
    assert r.status_code == 502
    assert "rate limit" in r.json()["error"]["message"] and "retry" in r.json()["error"]["message"]
    assert db.scalar(select(func.count()).select_from(ExecutiveReport)) == 0

    client = _client(db, SUPER, completer=FakeCompleter(error=RuntimeError("arn:aws:iam::123:role/secret")))
    r = client.post("/api/executive-reports/generate", json={"filters": Q1})
    assert r.status_code == 502 and "arn:aws" not in r.text


# ------------------------------------------------------------------ PDF
def test_pdf_contains_charts_and_summary_and_rejects_stale_filters(db, sales):
    import pdfplumber

    client = _client(db, SUPER)
    data = _generate(client)
    r = client.get(f"/api/executive-reports/{data['id']}/pdf", params=Q1)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert "Secondary_Sales" in r.headers["content-disposition"]
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert 2 <= len(pdf.pages) <= 6
        assert f"Page 1 of {len(pdf.pages)}" in text
    for expected in ("Quarterly Secondary Sales Report", "Q1 FY 2026-27", "Part A", "Sales Trend",
                     "Top Customers", "Product Contribution", "Part B", "1. Executive Overview",
                     "9. Closing Management Summary", "Kumar Rubber", "20.000", "IST"):
        assert expected in text, expected

    stale = client.get(f"/api/executive-reports/{data['id']}/pdf", params={**Q1, "period": "q2"})
    assert stale.status_code == 409
    other = client.get(f"/api/executive-reports/{data['id']}/pdf", params={**Q1, "distributor": "Beta Dist"})
    assert other.status_code == 409


def test_reports_are_private_to_their_creator(db, sales, monkeypatch):
    owner_row = User(username="owner", email="owner@x.test", full_name="Owner", role="user")
    other_row = User(username="other", email="other@x.test", full_name="Other", role="user")
    db.add_all([owner_row, other_row])
    db.flush()
    owner = RequestUser(role=UserRole.USER, name="Owner", user_id=owner_row.id)
    other = RequestUser(role=UserRole.USER, name="Other", user_id=other_row.id)
    data = _generate(_client(db, owner, monkeypatch=monkeypatch, segments=["Paper"]))
    assert data["can_email"] is False
    assert _client(db, owner).get(f"/api/executive-reports/{data['id']}/pdf", params=Q1).status_code == 200
    assert _client(db, other).get(f"/api/executive-reports/{data['id']}/pdf", params=Q1).status_code == 403
    assert _client(db, SUPER).get(f"/api/executive-reports/{data['id']}/pdf", params=Q1).status_code == 200


# ------------------------------------------------------------------ recipients
def test_recipient_configuration_is_admin_only_and_validated(db):
    user = RequestUser(role=UserRole.USER, name="u", user_id=None)
    assert _client(db, user).get("/api/executive-reports/recipients").status_code == 403
    assert _client(db, user).put("/api/executive-reports/recipients", json={"recipients": []}).status_code == 403

    admin = _client(db, SUPER)
    roles = [r["role"] for r in admin.get("/api/executive-reports/recipients").json()["data"]["recipients"]]
    assert roles == ["MD", "CFO", "CMO", "CIO"]
    bad = admin.put("/api/executive-reports/recipients", json={"recipients": [{"role": "MD", "email": "not-an-email"}]})
    assert bad.status_code == 422
    unknown = admin.put("/api/executive-reports/recipients", json={"recipients": [{"role": "CEO", "email": "a@b.com"}]})
    assert unknown.status_code == 422
    ok = admin.put("/api/executive-reports/recipients", json={"recipients": [
        {"role": "MD", "name": "Managing Director", "email": "md@example.com"},
        {"role": "CFO", "email": ""},
    ]})
    assert ok.status_code == 200
    saved = {r["role"]: r for r in ok.json()["data"]["recipients"]}
    assert saved["MD"]["email"] == "md@example.com" and saved["CFO"]["email"] is None
    assert saved["CMO"]["email"] is None


# ------------------------------------------------------------------ email
def _send(client, report_id, *, key="send-key-0001", recipients=None, filters=Q1):
    return client.post(f"/api/executive-reports/{report_id}/email", json={
        "filters": filters,
        "idempotency_key": key,
        "recipients": recipients or [
            {"role": "MD", "email": "md@example.com"},
            {"role": "CUSTOM", "name": "Sales Head", "email": "sales.head@example.com"},
        ],
    })


def test_email_attaches_pdf_and_blocks_duplicates(db, sales, monkeypatch):
    graph = FakeGraph()
    client = _client(db, SUPER, graph=graph, monkeypatch=monkeypatch)
    report_id = _generate(client)["id"]

    r = _send(client, report_id)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "SENT"
    assert len(graph.sent) == 1
    mail = graph.sent[0]
    assert mail["subject"] == "APCOTEX | Quarterly Secondary Sales Report | Q1 FY 2026-27"
    assert [t["email"] for t in mail["to"]] == ["md@example.com", "sales.head@example.com"]
    assert mail["attachments"][0]["content"].startswith(b"%PDF")
    assert mail["attachments"][0]["content_type"] == "application/pdf"
    assert "Dear Management Team" in mail["body_text"]

    again = _send(client, report_id)
    assert again.status_code == 200 and len(graph.sent) == 1
    rapid = _send(client, report_id, key="send-key-0002")
    assert rapid.status_code == 409 and len(graph.sent) == 1

    log = db.scalar(select(ExecutiveReportEmail))
    assert log.status == "SENT" and log.attachment_bytes > 0 and len(log.recipients) == 2
    audit = db.scalar(select(AuditTrail).where(AuditTrail.action == "Report Emailed"))
    assert audit is not None and audit.extra_metadata["recipients"][0]["email"] == "md@example.com"


def test_email_validation_staleness_and_permissions(db, sales, monkeypatch):
    graph = FakeGraph()
    client = _client(db, SUPER, graph=graph, monkeypatch=monkeypatch)
    report_id = _generate(client)["id"]
    assert _send(client, report_id, recipients=[{"role": "MD", "email": "bad@"}]).status_code == 422
    assert _send(client, report_id, filters={**Q1, "period": "q2"}).status_code == 409
    user = RequestUser(role=UserRole.USER, name="u", user_id=None)
    assert _send(_client(db, user), report_id).status_code == 403
    assert graph.sent == []


def test_email_delivery_failure_is_recorded_not_claimed(db, sales, monkeypatch):
    from app.exceptions import GraphAPIError

    graph = FakeGraph(error=GraphAPIError("Microsoft Graph API returned HTTP 403"))
    client = _client(db, SUPER, graph=graph, monkeypatch=monkeypatch)
    report_id = _generate(client)["id"]
    r = _send(client, report_id)
    assert r.status_code == 502 and "not sent" in r.json()["error"]["message"]
    log = db.scalar(select(ExecutiveReportEmail))
    assert log.status == "FAILED" and "HTTP 403" in log.error and log.sent_at is None
