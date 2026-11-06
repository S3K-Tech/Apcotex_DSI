"""Chat with Data: distinct entity counts, "list all" pagination and explicit entity keywords."""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.models.distributor import Distributor
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.schemas.chat_data import QueryDimension, QueryIntent, QueryMetric, QuerySpec
from app.services.access_control_service import DataScope
from app.services.chat_data.intent_service import RuleHints, RuleParser
from app.services.chat_data.orchestrator import ChatDataOrchestrator
from app.services.chat_data.query_service import ChatQueryService, ResolvedFilters
from app.services.chat_data.scope import ChatScope

ADMIN = ChatScope(True, "segment", None, None, None, "admin", "admin")
PAPER_ONLY = ChatScope(False, "segment", ["Paper"], None, None, "user", "paper-user")

DELTA = "Delta Rubber Co. Pvt. Ltd."
OMEGA = "Omega Minerals & Chemicals"
KAPPA = "Kappa Rubber Mills Private Limited"
BULK_CUSTOMERS = 231
DELTA_CUSTOMERS = BULK_CUSTOMERS + 1

_SETTINGS = get_settings().model_copy(
    update={"chat_data_use_llm": False, "chat_data_llm_phrasing": False, "chat_semantic_search_enabled": False}
)
_PRODUCTS = ["NITRILE 100", "LATEX 200", "BOND 300"]


@pytest.fixture()
def seeded(db):
    dists = {}
    for key, company in {"delta": DELTA, "omega": OMEGA}.items():
        d = Distributor(name=company, company=company)
        db.add(d)
        dists[key] = d
    db.flush()
    reports = {}

    def report(dist_key, label):
        if (dist_key, label) not in reports:
            r = Report(name=f"{dist_key} {label}", reporting_month=label,
                       distributor_id=dists[dist_key].id, status="completed")
            db.add(r)
            db.flush()
            reports[(dist_key, label)] = r
        return reports[(dist_key, label)]

    rows = []
    for i in range(1, BULK_CUSTOMERS + 1):
        rows.append(("delta", "FY 2025-26 • Q1", f"Bulk Customer {i:03d}", "Rubber", _PRODUCTS[i % 3],
                     str(i % 40 + 1), "Pune" if i % 2 else "Nashik", False))
    rows += [
        ("delta", "FY 2025-26 • Q1", KAPPA, "Rubber", "BOND 300", "500", "Pune", False),
        ("omega", "FY 2025-26 • Q2", KAPPA, "Paper", "NITRILE 100", "12", "Mumbai", False),
        ("omega", "FY 2025-26 • Q1", KAPPA, "Paper", "NITRILE 100", "99", "Mumbai", True),
        ("omega", "FY 2025-26 • Q1", "Paper Buyer One", "Paper", "LATEX 200", "6", "Mumbai", False),
        ("omega", "FY 2024-25 • Q1", "Old Buyer", "Paper", "LATEX 200", "4", "Mumbai", False),
    ]
    for i, (dk, label, cust, seg, prod, qty, loc, deleted) in enumerate(rows):
        db.add(
            SalesRecord(
                customer_name=cust, segment=seg, product=prod, quantity=Decimal(qty), unit="MT",
                period=label, location=loc, row_hash=hashlib.sha1(f"ecl{i}".encode()).hexdigest(),
                report_id=report(dk, label).id, distributor_id=dists[dk].id, is_deleted=deleted,
            )
        )
    db.flush()
    return dists


def _orch(db, scope):
    return ChatDataOrchestrator(db, scope, settings=_SETTINGS)


def ask(db, scope, question, previous=None):
    return _orch(db, scope).ask(question, previous)


def fact(answer, key):
    return next(f.value for f in answer.facts if f.key == key)


def names(answer):
    return [row["name"] for row in answer.table.rows] if answer.table else []


# ---- 1-3, 8: distinct counts, no table ------------------------------------------------


def test_count_distributors_for_customer(db, seeded):
    q1 = ask(db, ADMIN, f"How many distributors sell to {KAPPA} in Q1 FY 2025-26?")
    assert q1.status == "answer", q1.message
    assert fact(q1, "entity_count") == 1
    assert DELTA in q1.message
    assert q1.table is None and q1.pagination is None
    fy = ask(db, ADMIN, f"How many distributors sell to {KAPPA} in FY 2025-26?")
    assert fact(fy, "entity_count") == 2


def test_count_customers_for_distributor(db, seeded):
    a = ask(db, ADMIN, f"How many customers does {DELTA} have in FY 2025-26?")
    assert a.status == "answer", a.message
    assert fact(a, "entity_count") == DELTA_CUSTOMERS
    assert a.table is None and a.pagination is None


def test_count_products(db, seeded):
    a = ask(db, ADMIN, f"How many products does {DELTA} sell in FY 2025-26?")
    assert fact(a, "entity_count") == 3
    assert a.table is None


def test_count_without_dimension_asks_instead_of_rejecting(db, seeded):
    a = ask(db, ADMIN, f"How many for {DELTA} in FY 2025-26?")
    assert a.status == "clarification"


# ---- 4: explicit entity keywords ----------------------------------------------------


def test_explicit_customer_keyword_resolves_customer(db, seeded):
    a = ask(db, ADMIN, "Show data for Customer Kappa Rubber Mills in FY 2025-26")
    assert a.status == "answer", a.message
    assert a.filters == {"Customer": KAPPA}
    assert fact(a, "quantity_mt") == pytest.approx(512.0)


def test_explicit_customer_not_found_is_customer_specific(db, seeded):
    a = ask(db, ADMIN, "Show data for Customer Zzyzx Holdings in FY 2025-26")
    assert a.status == "no_data"
    assert "customer" in a.message and "distributor" not in a.message.lower()


def test_fuzzy_customer_is_confirmed_not_silently_picked(db, seeded):
    a = ask(db, ADMIN, "Show data for Customer Kapa Rubbr Mils Private Limited in FY 2025-26")
    assert a.status == "clarification"
    assert KAPPA in a.clarification.options


def test_explicit_distributor_keyword_parses_as_distributor():
    spec = RuleParser().parse("Show data for Distributor Goel Minerals in FY 2025-26")
    assert spec.filters.distributor == "Goel Minerals" and spec.filters.customer is None
    ranked = RuleParser().parse("Distributor Goel Minerals top 5 customers in FY 2025-26")
    assert ranked.filters.distributor == "Goel Minerals" and ranked.limit == 5


# ---- 5-6: list all with pagination ----------------------------------------------------


def test_list_all_customers_returns_full_paginated_set(db, seeded):
    first = ask(db, ADMIN, f"List all customers of {DELTA} in FY 2025-26")
    assert first.status == "answer", first.message
    assert first.spec.intent == QueryIntent.LIST
    p = first.pagination
    assert (p.total_records, p.current_page, p.page_size, p.total_pages) == (DELTA_CUSTOMERS, 1, 50, 5)
    assert (p.first_record, p.last_record, p.entity) == (1, 50, "customers")
    assert len(first.table.rows) == 50
    assert fact(first, "group_count") == DELTA_CUSTOMERS

    orch = _orch(db, ADMIN)
    collected = list(first.table.rows)
    for n in range(2, p.total_pages + 1):
        page = orch.page(first.spec, n)
        assert page.status == "answer", page.message
        assert page.pagination.current_page == n
        assert page.pagination.total_records == DELTA_CUSTOMERS
        collected += page.table.rows
    assert len(collected) == DELTA_CUSTOMERS
    assert len({r["name"] for r in collected}) == DELTA_CUSTOMERS
    assert [r["rank"] for r in collected] == list(range(1, DELTA_CUSTOMERS + 1))
    assert collected[0]["name"] == KAPPA


def test_pagination_matches_full_ordering_and_totals(db, seeded):
    first = ask(db, ADMIN, f"List all customers of {DELTA} in FY 2025-26")
    orch = _orch(db, ADMIN)
    qs = ChatQueryService(db, ADMIN)
    rf = ResolvedFilters(distributor_id=seeded["delta"].id, distributor_name=DELTA)
    period = first.spec.periods[0]
    expected = [g.label for g in qs.grouped(rf, period, QueryDimension.CUSTOMER)]
    total = qs.total(rf, period).quantity_mt

    last = orch.page(first.spec, 5)
    assert len(last.table.rows) == DELTA_CUSTOMERS - 200
    assert (last.pagination.first_record, last.pagination.last_record) == (201, DELTA_CUSTOMERS)
    assert names(last) == expected[200:]
    assert names(orch.page(first.spec, 3)) == expected[100:150]
    again = orch.page(first.spec, 3)
    assert names(again) == expected[100:150]

    share_sum = sum(r["share_pct"] for n in range(1, 6) for r in orch.page(first.spec, n).table.rows)
    assert share_sum == pytest.approx(100.0, abs=1.0)
    assert fact(first, "total_mt") == pytest.approx(total)

    wide = orch.page(first.spec, 1, page_size=100)
    assert wide.pagination.total_pages == 3 and len(wide.table.rows) == 100
    beyond = orch.page(first.spec, 99)
    assert beyond.pagination.current_page == 5


def test_page_request_does_not_reinterpret(db, seeded, monkeypatch):
    first = ask(db, ADMIN, f"List all customers of {DELTA} in FY 2025-26")

    def boom(*_a, **_k):
        raise AssertionError("intent detection must not run on page change")

    monkeypatch.setattr("app.services.chat_data.orchestrator.IntentService.interpret", boom)
    assert _orch(db, ADMIN).page(first.spec, 2).status == "answer"


def test_only_list_answers_can_be_paged(db, seeded):
    spec = QuerySpec(intent=QueryIntent.RANK, dimension=QueryDimension.CUSTOMER,
                     periods=[{"fy_start": 2025}])
    assert _orch(db, ADMIN).page(spec, 2).status == "error"


# ---- 7: top N unchanged -------------------------------------------------------------


def test_top_ten_still_returns_exactly_ten(db, seeded):
    a = ask(db, ADMIN, f"Top 10 customers of {DELTA} in FY 2025-26")
    assert a.spec.intent == QueryIntent.RANK
    assert len(a.table.rows) == 10 and a.pagination is None
    assert names(a)[0] == KAPPA


# ---- 9: filters preserved -------------------------------------------------------------


def test_list_preserves_quarter_location_and_page_filters(db, seeded):
    a = ask(db, ADMIN, f"List all customers of {DELTA} in location Pune in Q1 FY 2025-26")
    assert a.status == "answer", a.message
    pune = (BULK_CUSTOMERS + 1) // 2 + 1
    assert a.pagination.total_records == pune
    assert a.filters.get("Location") == "Pune"
    page2 = _orch(db, ADMIN).page(a.spec, 2)
    assert page2.pagination.total_records == pune
    assert page2.filters == a.filters


def test_list_distributors_for_customer_and_segment(db, seeded):
    a = ask(db, ADMIN, f"Which distributors sell to {KAPPA} in FY 2025-26?")
    assert a.spec.intent == QueryIntent.LIST
    assert sorted(names(a)) == sorted([DELTA, OMEGA])
    seg = ask(db, ADMIN, "List all distributors in the Paper segment in FY 2025-26")
    assert names(seg) == [OMEGA]


def test_count_with_product_filter(db, seeded):
    a = ask(db, ADMIN, f"How many customers bought BOND 300 from {DELTA} in FY 2025-26?")
    expected = len([i for i in range(1, BULK_CUSTOMERS + 1) if i % 3 == 2]) + 1
    assert fact(a, "entity_count") == expected


def test_follow_up_list_inherits_customer(db, seeded):
    first = ask(db, ADMIN, "Show data for Customer Kappa Rubber Mills in FY 2025-26")
    a = ask(db, ADMIN, "Which distributor sells to this customer?", previous=first.spec)
    assert a.status == "answer", a.message
    assert sorted(names(a)) == sorted([DELTA, OMEGA])


# ---- 10: authorization and soft deletes ----------------------------------------------


def test_counts_respect_scope_and_soft_delete(db, seeded):
    a = ask(db, PAPER_ONLY, f"How many distributors sell to {KAPPA} in FY 2025-26?")
    assert fact(a, "entity_count") == 1 and DELTA not in a.message
    deleted_only = ask(db, PAPER_ONLY, f"How many distributors sell to {KAPPA} in Q1 FY 2025-26?")
    assert deleted_only.status == "no_data"


def test_page_is_reauthorized(db, seeded):
    admin = ask(db, ADMIN, f"List all customers of {DELTA} in FY 2025-26")
    forged = _orch(db, PAPER_ONLY).page(admin.spec, 2)
    assert forged.status != "answer"
    assert not forged.table


def test_page_endpoint_uses_server_scope(db, seeded, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import chat_data as endpoint
    from app.database.session import get_db
    from app.dependencies.rbac import RequestUser, get_current_user
    from app.enums import UserRole
    from app.exceptions import AppException, app_exception_handler

    settings = _SETTINGS.model_copy(update={"chat_data_enabled": True, "chat_data_rate_limit_per_minute": 100})
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.chat_data.orchestrator.get_settings", lambda: settings)
    scope = DataScope(allowed_segments=["Paper"], access_mode="segment", unrestricted=False)
    monkeypatch.setattr(endpoint, "data_scope_for_user", lambda _u, _db: scope)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(endpoint.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    spec = ask(db, ADMIN, f"List all customers of {DELTA} in FY 2025-26").spec.model_dump(mode="json")
    assert client.post("/api/chat-data/page", json={"spec": spec, "page": 2}).status_code == 401

    app.dependency_overrides[get_current_user] = lambda: RequestUser(role=UserRole("user"), name="t", user_id=None)
    r = client.post("/api/chat-data/page", json={"spec": spec, "page": 2})
    assert r.status_code == 200 and r.json()["data"]["status"] != "answer"
    too_big = client.post("/api/chat-data/page", json={"spec": spec, "page": 1, "page_size": 10_000})
    assert too_big.status_code == 422


# ---- intent separation (rules) --------------------------------------------------------


def test_intents_stay_distinct():
    p = RuleParser(RuleHints(distributor_names=[DELTA]))
    top = p.parse(f"Top 10 customers of {DELTA} in FY 2025-26")
    listed = p.parse(f"List all customers of {DELTA} in FY 2025-26")
    count = p.parse(f"How many customers does {DELTA} have in FY 2025-26?")
    total = p.parse(f"What is the total quantity for all customers of {DELTA} in FY 2025-26?")
    assert top.intent == QueryIntent.RANK and top.limit == 10
    assert listed.intent == QueryIntent.LIST and listed.dimension == QueryDimension.CUSTOMER
    assert count.metric == QueryMetric.ENTITY_COUNT and count.dimension == QueryDimension.CUSTOMER
    assert total.intent == QueryIntent.AGGREGATE and total.metric == QueryMetric.QUANTITY_MT
    sells = p.parse(f"How many distributors sell to {KAPPA} in FY 2025-26?")
    assert sells.dimension == QueryDimension.DISTRIBUTOR and sells.filters.customer == KAPPA
