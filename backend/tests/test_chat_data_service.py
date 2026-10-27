"""Chat with Data against the disposable test database: scope, leakage, analytics semantics."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.distributor import Distributor
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.schemas.chat_data import QuerySpec
from app.services.access_control_service import DataScope
from app.services.chat_data.orchestrator import ChatDataOrchestrator
from app.services.chat_data.query_service import ChatQueryService, ResolvedFilters
from app.services.chat_data.scope import RESTRICTED_SEGMENT_MESSAGE, ChatScope

ADMIN = ChatScope(True, "segment", None, None, None, "admin", "admin")
PAPER_CARPET = ChatScope(False, "segment", ["Paper", "Carpet"], None, None, "user", "paper-user")
BETA_ONLY = ChatScope(False, "distributor", None, ["Beta Chemicals"], None, "user", "beta-user")
NO_ACCESS = ChatScope(False, "segment", [], None, None, "user", "nobody")

_SETTINGS = get_settings().model_copy(
    update={
        "chat_data_use_llm": False,
        "chat_data_llm_phrasing": False,
        "chat_semantic_search_enabled": False,
    }
)


@pytest.fixture()
def seeded(db):
    dists = {}
    for key, company in {
        "alpha": "Alpha Traders Pvt Ltd",
        "beta": "Beta Chemicals",
        "gamma": "Gamma Secret Corp",
        "alpha2": "Alpha Traders Mumbai",
    }.items():
        d = Distributor(name=company, company=company)
        db.add(d)
        dists[key] = d
    db.flush()
    reports = {}

    def report(dist_key, label):
        k = (dist_key, label)
        if k not in reports:
            r = Report(
                name=f"{dist_key} {label}",
                reporting_month=label,
                distributor_id=dists[dist_key].id if dist_key else None,
                status="completed",
            )
            db.add(r)
            db.flush()
            reports[k] = r
        return reports[k]

    rows = [
        # dist, report label, customer, segment, product, qty, unit, source_month
        ("alpha", "FY 2026-27 • Q1", "Acme Papers", "Paper", "APCOFLEX N-385", "10", "MT", "April 2026"),
        ("alpha", "FY 2026-27 • Q1", "ACME  papers", "Paper", "Apcoflex N385", "5", "MT", "May 2026"),
        ("alpha", "FY 2026-27 • Q1", "Zen Carpets", "Carpet", "CARPETBOND 1", "8000", "KG", "June 2026"),
        ("alpha", "FY 2026-27 • Q1", "Unit Buyer", "Paper", "APCOFLEX N-385", "100", "Units", "April 2026"),
        ("alpha", "FY 2026-27 • Q2", "Acme Papers", "Paper", "APCOFLEX N385", "20", "MT", None),
        ("alpha", "FY 2026-27 • Q2", "Zen Carpets", "Carpet", "CARPETBOND 1", "2", "MT", None),
        ("alpha", "FY 2025-26 • Q4", "Acme Papers", "Paper", "APCOFLEX N385", "7", "MT", "March 2026"),
        ("beta", "FY 2026-27 • Q1", "Rubber King", "Rubber", "NBR LATEX 200", "30", "MT", "April 2026"),
        ("gamma", "FY 2026-27 • Q1", "Secret Glove Co", "Gloves", "GLOVETEX 9", "50", "MT", "April 2026"),
        ("alpha2", "FY 2024-25 • Q1", "Mumbai Buyer", "Construction", "BUILDTEX", "4", "MT", "April 2024"),
        (None, "FY 2026-27 • Q1", "Orphan Buyer", "Paper", "APCOFLEX N385", "3", "MT", "April 2026"),
    ]
    for i, (dk, label, cust, seg, prod, qty, unit, month) in enumerate(rows):
        r = report(dk, label)
        db.add(
            SalesRecord(
                customer_name=cust,
                segment=seg,
                product=prod,
                quantity=Decimal(qty),
                unit=unit,
                period=label,
                source_month=month,
                row_hash=hashlib.sha1(f"{i}".encode()).hexdigest(),
                report_id=r.id,
                distributor_id=dists[dk].id if dk else None,
            )
        )
    db.flush()
    return dists


def ask(db, scope, question, previous=None, **kw):
    orch = ChatDataOrchestrator(db, scope, settings=kw.pop("settings", _SETTINGS), **kw)
    return orch.ask(question, previous)


def names(answer):
    return [row.get("name") for row in (answer.table.rows if answer.table else [])]


# ---- totals, units, FY boundaries -------------------------------------------------


def test_admin_total_quarter(db, seeded):
    a = ask(db, ADMIN, "Total quantity in Q1 FY 2026-27")
    assert a.status == "answer", a.message
    assert a.facts[0].value == pytest.approx(103.0)
    assert "103.000 MT" in a.message
    assert any("piece-count" in c for c in a.caveats)
    assert any("no linked distributor" in c for c in a.caveats)


def test_kg_rows_convert_once_and_units_contribute_zero(db, seeded):
    a = ask(db, ADMIN, "Total quantity for Alpha Traders in Q1 FY 2026-27")
    assert a.filters == {"Distributor": "Alpha Traders Pvt Ltd"}
    assert a.facts[0].value == pytest.approx(23.0)


def test_fy_boundary_march_belongs_to_previous_fy(db, seeded):
    prev = ask(db, ADMIN, "Total quantity for Alpha Traders in FY 2025-26")
    assert prev.facts[0].value == pytest.approx(7.0)
    march = ask(db, ADMIN, "Total quantity for Alpha Traders in March 2026")
    assert march.facts[0].value == pytest.approx(7.0)
    q4 = ask(db, ADMIN, "Total quantity for Alpha Traders in Q4 FY 2026-27")
    assert q4.status == "no_data"


def test_no_data_wording(db, seeded):
    a = ask(db, ADMIN, "Total quantity in FY 2030-31")
    assert a.status == "no_data"
    assert a.message.startswith("No sales data was found")


# ---- ranking / normalization / comparisons / trends --------------------------------


def test_product_normalization_merges_grade_hyphen(db, seeded):
    a = ask(db, ADMIN, "Top products for Alpha Traders in FY 2026-27")
    assert a.status == "answer"
    n385 = [r for r in a.table.rows if "N385" in r["name"].replace("-", "")]
    assert len(n385) == 1 and n385[0]["quantity_mt"] == pytest.approx(35.0)


def test_top_customers_group_case_variants(db, seeded):
    a = ask(db, ADMIN, "Top 2 customers of Alpha Traders in FY 2026-27")
    assert a.table.rows[0]["quantity_mt"] == pytest.approx(35.0)
    assert len(a.table.rows) == 2


def test_comparison(db, seeded):
    a = ask(db, ADMIN, "Compare Q1 vs Q2 FY 2026-27 for Alpha Traders")
    assert a.status == "answer"
    by_key = {f.key: f.value for f in a.facts}
    assert by_key["period1_mt"] == pytest.approx(23.0)
    assert by_key["period2_mt"] == pytest.approx(22.0)
    assert by_key["change_mt"] == pytest.approx(-1.0)


def test_monthly_trend_uses_only_real_months(db, seeded):
    a = ask(db, ADMIN, "Monthly trend for Alpha Traders in FY 2026-27")
    assert a.status == "answer"
    months = [r["month"] for r in a.table.rows]
    assert months == ["April 2026", "May 2026", "June 2026"]
    assert [r["quantity_mt"] for r in a.table.rows] == pytest.approx([10.0, 5.0, 8.0])
    assert any("not spread across months" in c for c in a.caveats)


def test_explain_change_lists_contributors_without_causes(db, seeded):
    a = ask(db, ADMIN, "Why did Alpha Traders change between Q1 and Q2 FY 2026-27?")
    assert a.status == "answer"
    assert "does not record why" in a.message
    assert any("acme papers" in r["name"].casefold() for r in a.table.rows)


# ---- scope isolation ----------------------------------------------------------------


def test_segment_user_sees_only_assigned_segments(db, seeded):
    a = ask(db, PAPER_CARPET, "Total quantity in FY 2026-27")
    assert a.facts[0].value == pytest.approx(45.0)
    top = ask(db, PAPER_CARPET, "Top 10 customers in FY 2026-27")
    assert "Rubber King" not in names(top) and "Secret Glove Co" not in names(top)
    dists = ask(db, PAPER_CARPET, "Top distributors in FY 2026-27")
    assert names(dists) == ["Alpha Traders Pvt Ltd"]


def test_explicit_unauthorized_segment_is_restricted(db, seeded):
    a = ask(db, PAPER_CARPET, "Total quantity in Gloves segment in FY 2026-27")
    assert a.status == "restricted" and a.message == RESTRICTED_SEGMENT_MESSAGE
    assert a.table is None and not a.facts


def test_unauthorized_distributor_is_indistinguishable_from_missing(db, seeded):
    hidden = ask(db, PAPER_CARPET, "Top customers of Gamma Secret Corp in FY 2026-27")
    missing = ask(db, PAPER_CARPET, "Top customers of Nonexistent Holdings in FY 2026-27")
    assert hidden.status == missing.status == "no_data"
    assert hidden.message.replace("Gamma Secret Corp", "X") == missing.message.replace("Nonexistent Holdings", "X")
    assert not hidden.table and not hidden.facts


def test_unauthorized_customer_and_product_do_not_leak(db, seeded):
    cust = ask(db, PAPER_CARPET, 'Total quantity for customer "Secret Glove Co" in FY 2026-27')
    missing = ask(db, PAPER_CARPET, 'Total quantity for customer "Nobody Such Ltd" in FY 2026-27')
    assert cust.status == missing.status == "no_data"
    assert not cust.facts and cust.table is None
    assert cust.message.replace("Secret Glove Co", "X") == missing.message.replace("Nobody Such Ltd", "X")
    prod = ask(db, BETA_ONLY, "Total quantity of APCOFLEX N385 in FY 2026-27")
    fake = ask(db, BETA_ONLY, "Total quantity of ZZTEX N999 in FY 2026-27")
    assert prod.status == fake.status == "no_data"
    assert not prod.facts and prod.table is None
    assert prod.message.replace("APCOFLEX N385", "X") == fake.message.replace("ZZTEX N999", "X")


def test_distributor_mode_user_is_limited_to_assigned_companies(db, seeded):
    a = ask(db, BETA_ONLY, "Total quantity in FY 2026-27")
    assert a.facts[0].value == pytest.approx(30.0)
    other = ask(db, BETA_ONLY, "Top customers of Alpha Traders in FY 2026-27")
    assert other.status == "no_data"
    top = ask(db, BETA_ONLY, "Top customers in FY 2026-27")
    assert names(top) == ["Rubber King"]


def test_admin_sees_all_distributors(db, seeded):
    a = ask(db, ADMIN, "Top distributors in FY 2026-27")
    assert set(names(a)) == {"Alpha Traders Pvt Ltd", "Beta Chemicals", "Gamma Secret Corp"}


def test_user_without_assignments_gets_no_data(db, seeded):
    a = ask(db, NO_ACCESS, "Total quantity in FY 2026-27")
    assert a.status == "no_data" and not a.facts


def test_previous_spec_is_reauthorized(db, seeded):
    forged = QuerySpec.model_validate(
        {"intent": "aggregate", "filters": {"segment": "Gloves"}, "periods": [{"fy_start": 2026}]}
    )
    a = ask(db, PAPER_CARPET, "What about Q2 FY 2026-27?", forged)
    assert a.status == "restricted"
    forged_dist = QuerySpec.model_validate(
        {"intent": "aggregate", "filters": {"distributor": "Gamma Secret Corp"}, "periods": [{"fy_start": 2026}]}
    )
    b = ask(db, PAPER_CARPET, "And Q1 FY 2026-27?", forged_dist)
    assert b.status == "no_data" and not b.facts


def test_llm_output_cannot_widen_scope(db, seeded):
    spec = {"intent": "aggregate", "filters": {"segment": "Gloves"}, "periods": [{"fy_start": 2026}]}
    completion = SimpleNamespace(text=json.dumps(spec), model_id="stub", usage=lambda: {"total_tokens": 0})
    settings = _SETTINGS.model_copy(update={"chat_data_use_llm": True})
    a = ask(db, PAPER_CARPET, "Total in FY 2026-27", settings=settings, completer=lambda **_: completion)
    assert a.status == "restricted" and a.interpreted_by == "llm"


# ---- clarifications and follow-ups --------------------------------------------------


def test_ambiguous_distributor_asks_to_choose(db, seeded):
    a = ask(db, ADMIN, "Top customers of Alpha in FY 2026-27")
    assert a.status == "clarification"
    assert set(a.clarification.options) == {"Alpha Traders Mumbai", "Alpha Traders Pvt Ltd"}
    chosen = ask(db, ADMIN, "Alpha Traders Pvt Ltd", a.spec)
    assert chosen.status == "answer" and chosen.filters == {"Distributor": "Alpha Traders Pvt Ltd"}


def test_missing_period_clarification_then_follow_up(db, seeded):
    a = ask(db, ADMIN, "Top customers in Q1")
    assert a.status == "clarification" and "FY 2026-27" in a.clarification.options
    b = ask(db, ADMIN, "FY 2026-27", a.spec)
    assert b.status == "answer" and b.periods == ["FY 2026–27 • Q1"]


def test_follow_up_keeps_filters(db, seeded):
    first = ask(db, ADMIN, "Top 5 customers of Alpha Traders in FY 2026-27")
    second = ask(db, ADMIN, "What about Q2 FY 2026-27?", first.spec)
    assert second.filters == {"Distributor": "Alpha Traders Pvt Ltd"}
    assert second.periods == ["FY 2026–27 • Q2"]


def test_unsupported_answer(db, seeded):
    a = ask(db, ADMIN, "What was the revenue in FY 2026-27?")
    assert a.status == "unsupported"


# ---- SQL safety ---------------------------------------------------------------------


def test_user_text_is_bound_not_interpolated(db, seeded):
    qs = ChatQueryService(db, ADMIN)
    evil = "Paper' OR 1=1 --"
    compiled = str(qs.scoped(select(func.count(SalesRecord.id)), ResolvedFilters(segment=evil)).compile())
    assert "1=1" not in compiled
    a = ask(db, ADMIN, "Top customers of Xyz' OR '1'='1 in FY 2026-27")
    assert a.status == "no_data" and not a.table
    refused = ask(db, ADMIN, "Top customers of X'); DROP TABLE sales_records;-- in FY 2026-27")
    assert refused.status == "unsupported"
    assert db.execute(select(func.count(SalesRecord.id))).scalar() == 11


# ---- endpoint -----------------------------------------------------------------------


def _client(db, monkeypatch, *, enabled, user=None, scope=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import chat_data as endpoint
    from app.database.session import get_db
    from app.dependencies.rbac import get_current_user
    from app.exceptions import AppException, app_exception_handler

    settings = _SETTINGS.model_copy(update={"chat_data_enabled": enabled, "chat_data_rate_limit_per_minute": 100})
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.chat_data.orchestrator.get_settings", lambda: settings)
    if scope is not None:
        monkeypatch.setattr(endpoint, "data_scope_for_user", lambda _u, _db: scope)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _request_user(role="user"):
    from app.dependencies.rbac import RequestUser
    from app.enums import UserRole

    return RequestUser(role=UserRole(role), name="tester", user_id=None)


def test_endpoint_requires_authentication(db, seeded, monkeypatch):
    client = _client(db, monkeypatch, enabled=True)
    r = client.post("/api/chat-data/ask", json={"question": "Total in FY 2026-27"})
    assert r.status_code == 401


def test_endpoint_disabled_by_default_flag(db, seeded, monkeypatch):
    client = _client(db, monkeypatch, enabled=False, user=_request_user())
    r = client.post("/api/chat-data/ask", json={"question": "Total in FY 2026-27"})
    assert r.status_code == 503
    status = client.get("/api/chat-data/status").json()["data"]
    assert status["enabled"] is False and status["suggestions"] == []


def test_endpoint_uses_server_scope_and_rejects_client_fields(db, seeded, monkeypatch):
    scope = DataScope(allowed_segments=["Paper", "Carpet"], access_mode="segment", unrestricted=False)
    client = _client(db, monkeypatch, enabled=True, user=_request_user(), scope=scope)
    r = client.post("/api/chat-data/ask", json={"question": "Total quantity in FY 2026-27"})
    assert r.status_code == 200
    assert r.json()["data"]["facts"][0]["value"] == pytest.approx(45.0)
    forged = client.post(
        "/api/chat-data/ask", json={"question": "Total in FY 2026-27", "allowed_segments": ["Gloves"]}
    )
    assert forged.status_code == 422
    status = client.get("/api/chat-data/status").json()["data"]
    assert status["enabled"] is True
    assert not any("Gamma" in s or "Beta" in s for s in status["suggestions"])
