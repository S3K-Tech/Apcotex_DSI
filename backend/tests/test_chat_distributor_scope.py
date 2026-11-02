"""Chat with Data: distributor isolation, optional quarters and report-footer rows (test DB only)."""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.erp_parser.stock_item_parser import _SKIP_ROW_RE
from app.models.distributor import Distributor
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.schemas.chat_data import PeriodRef, QueryDimension
from app.services.chat_data.orchestrator import ChatDataOrchestrator
from app.services.chat_data.query_service import ChatQueryService, ResolvedFilters
from app.services.chat_data.scope import ChatScope
from app.utils.summary_labels import is_report_footer_label

ADMIN = ChatScope(True, "segment", None, None, None, "admin", "admin")
CARPET_ONLY = ChatScope(False, "segment", ["Carpet"], None, None, "user", "carpet-user")

FOOTER = "Totals as per 'Default' valuation :"
GOEL = "GOEL MINERALS & CHEMICALS"

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
        "goel": GOEL,
        "altek": "ALTEK INTERNATIONAL FZE",
        "reda": "REDA INDUSTRIAL MATERIALS",
        "bajaj_chem": "BAJAJ CHEMICALS",
        "bajaj_rubber": "BAJAJ RUBBER COMPANY PVT. LTD.",
    }.items():
        d = Distributor(name=company, company=company)
        db.add(d)
        dists[key] = d
    db.flush()
    reports = {}

    def report(dist_key, label):
        k = (dist_key, label)
        if k not in reports:
            r = Report(name=f"{dist_key} {label}", reporting_month=label,
                       distributor_id=dists[dist_key].id, status="completed")
            db.add(r)
            db.flush()
            reports[k] = r
        return reports[k]

    rows = [
        # dist, report label, customer, segment, qty, unit, source_month, deleted
        ("goel", "FY 2026-27 • Q1", "UTTAM POLYRUBS INDIA", "Rubber", "3000", "KG", "April 2026", False),
        ("goel", "FY 2026-27 • Q1", "Cosco India Limited", "Rubber", "2000", "KG", "May 2026", False),
        ("goel", "FY 2026-27 • Q1", "Shared Buyer", "Rubber", "1000", "KG", "June 2026", False),
        ("goel", "FY 2026-27 • Q1", "Total Rubber Co", "Rubber", "500", "KG", "June 2026", False),
        ("goel", "FY 2026-27 • Q1", FOOTER, "Rubber", "6500", "KG", None, False),
        ("goel", "FY 2026-27 • Q1", "Deleted Buyer", "Rubber", "99000", "KG", "May 2026", True),
        ("goel", "FY 2026-27 • Q2", "UTTAM POLYRUBS INDIA", "Rubber", "6000", "KG", "July 2026", False),
        ("goel", "FY 2026-27 • Q2", "Cosco India Limited", "Rubber", "4800", "KG", "August 2026", False),
        ("goel", "FY 2025-26 • Q4", "UTTAM POLYRUBS INDIA", "Rubber", "9", "MT", "March 2026", False),
        ("altek", "FY 2026-27 • Q1", "Altek Total Sales", "Carpet", "2297.14", "MT", "April 2026", False),
        ("altek", "FY 2026-27 • Q1", "Shared Buyer", "Carpet", "50", "MT", "May 2026", False),
        ("reda", "FY 2026-27 • Q1", "REDA Total", "Carpet", "121.2", "MT", "April 2026", False),
        ("reda", "FY 2026-27 • Q1", "Shared Buyer", "Carpet", "7", "MT", "May 2026", False),
        ("bajaj_chem", "FY 2026-27 • Q1", "Beta Two", "Rubber", "1", "MT", "April 2026", False),
        ("bajaj_chem", "FY 2026-27 • Q1", "Alpha One", "Rubber", "1", "MT", "April 2026", False),
        ("bajaj_chem", "FY 2026-27 • Q1", "   ", "Rubber", "0.5", "MT", "April 2026", False),
        ("bajaj_rubber", "FY 2026-27 • Q1", "Rubber Buyer", "Rubber", "2", "MT", "April 2026", False),
    ]
    for i, (dk, label, cust, seg, qty, unit, month, deleted) in enumerate(rows):
        db.add(
            SalesRecord(
                customer_name=cust,
                segment=seg,
                product="APCOTEX SR 558",
                quantity=Decimal(qty),
                unit=unit,
                original_unit=unit,
                period=label,
                source_month=month,
                row_hash=hashlib.sha1(f"scope-{i}".encode()).hexdigest(),
                report_id=report(dk, label).id,
                distributor_id=dists[dk].id,
                is_deleted=deleted,
            )
        )
    db.flush()
    return dists


def ask(db, scope, question, previous=None):
    return ChatDataOrchestrator(db, scope, settings=_SETTINGS).ask(question, previous)


def names(answer):
    return [row.get("name") for row in (answer.table.rows if answer.table else [])]


def facts(answer):
    return {f.key: f.value for f in answer.facts}


# ---- the reported incident -------------------------------------------------------------


def test_goel_partial_name_then_financial_year_ranks_only_goel(db, seeded):
    first = ask(db, ADMIN, "Goel minerals top 5 customers")
    assert first.status == "clarification"
    assert {"FY 2026-27", "FY 2025-26", "All available periods"} <= set(first.clarification.options)
    assert first.spec.filters.distributor == "Goel minerals"

    a = ask(db, ADMIN, "FY 2026-27", first.spec)
    assert a.status == "answer", a.message
    assert a.filters == {"Distributor": GOEL}
    assert names(a) == ["UTTAM POLYRUBS INDIA", "Cosco India Limited", "Shared Buyer", "Total Rubber Co"]
    assert "Altek Total Sales" not in names(a) and "REDA Total" not in names(a)
    assert not any(row.get("distributor") for row in a.table.rows)
    f = facts(a)
    assert f["total_mt"] == pytest.approx(17.3)
    assert f["group_count"] == 4
    assert [r["share_pct"] for r in a.table.rows] == [52.0, 39.3, 5.8, 2.9]
    assert "Period: Full financial year FY 2026–27 (April 2026 – March 2027)." in a.message
    assert a.periods == ["FY 2026–27"]
    assert a.refinements == ["Q1 FY 2026-27", "Q2 FY 2026-27", "Q3 FY 2026-27", "Q4 FY 2026-27"]


def test_shared_customer_name_stays_within_distributor(db, seeded):
    a = ask(db, ADMIN, "Goel minerals top 10 customers in Q1 FY 2026-27")
    shared = next(r for r in a.table.rows if r["name"] == "Shared Buyer")
    assert shared["quantity_mt"] == pytest.approx(1.0)


def test_total_count_ranking_and_denominator_share_one_scope(db, seeded):
    a = ask(db, ADMIN, "Top 50 customers of Goel Minerals in FY 2026-27")
    f = facts(a)
    assert sum(r["quantity_mt"] for r in a.table.rows) == pytest.approx(f["total_mt"])
    assert f["group_count"] == len(a.table.rows)
    qs = ChatQueryService(db, ADMIN)
    rf = ResolvedFilters(distributor_id=seeded["goel"].id)
    total = qs.total(rf, PeriodRef(fy_start=2026))
    assert total.quantity_mt == pytest.approx(f["total_mt"]) and total.customer_count == f["group_count"]


# ---- distributor resolution --------------------------------------------------------------


def test_ambiguous_distributor_asks_instead_of_choosing(db, seeded):
    a = ask(db, ADMIN, "Bajaj top customers in FY 2026-27")
    assert a.status == "clarification" and a.table is None
    assert set(a.clarification.options) == {"BAJAJ CHEMICALS", "BAJAJ RUBBER COMPANY PVT. LTD."}
    chosen = ask(db, ADMIN, "BAJAJ RUBBER COMPANY PVT. LTD.", a.spec)
    assert chosen.status == "answer" and names(chosen) == ["Rubber Buyer"]


def test_unknown_leading_distributor_does_not_rank_everyone(db, seeded):
    a = ask(db, ADMIN, "Xyz Traders top 5 customers in FY 2026-27")
    assert a.status == "no_data" and a.table is None and not a.facts
    assert "Xyz Traders" in a.message


def test_fuzzy_distributor_match_is_confirmed_not_silent(db, seeded):
    a = ask(db, ADMIN, "Top customers of Goal Minerals & Chemicals in FY 2026-27")
    assert a.status == "clarification" and a.table is None
    assert a.clarification.options == [GOEL]
    assert "Did you mean" in a.message
    chosen = ask(db, ADMIN, GOEL, a.spec)
    assert chosen.status == "answer" and chosen.filters == {"Distributor": GOEL}


def test_unauthorized_distributor_gives_no_fallback_ranking(db, seeded):
    a = ask(db, CARPET_ONLY, "Goel minerals top 5 customers in FY 2026-27")
    assert a.status == "no_data" and a.table is None and not a.facts


def test_soft_deleted_rows_stay_excluded(db, seeded):
    a = ask(db, ADMIN, "Top 50 customers of Goel Minerals in Q1 FY 2026-27")
    assert "Deleted Buyer" not in names(a)
    assert facts(a)["total_mt"] == pytest.approx(6.5)


# ---- periods -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,total,statement",
    [
        ("Q1 FY 2026-27", 6.5, "Period: Q1 FY 2026–27 (April 2026 – June 2026)."),
        ("Q2 FY 2026-27", 10.8, "Period: Q2 FY 2026–27 (July 2026 – September 2026)."),
        ("Q4 FY 2025-26", 9.0, "Period: Q4 FY 2025–26 (January 2026 – March 2026)."),
    ],
)
def test_quarter_follow_up_keeps_distributor(db, seeded, question, total, statement):
    year = ask(db, ADMIN, "Goel minerals top 5 customers in FY 2026-27")
    a = ask(db, ADMIN, question, year.spec)
    assert a.status == "answer" and a.filters == {"Distributor": GOEL}
    assert facts(a)["total_mt"] == pytest.approx(total)
    assert statement in a.message and a.refinements == []


def test_quarter_without_data_is_empty_not_annual(db, seeded):
    a = ask(db, ADMIN, "Goel minerals top 5 customers in Q3 FY 2026-27")
    assert a.status == "no_data" and a.table is None


def test_bare_quarter_follow_up_uses_previous_financial_year(db, seeded):
    year = ask(db, ADMIN, "Goel minerals top 5 customers in FY 2026-27")
    a = ask(db, ADMIN, "Q2", year.spec)
    assert a.status == "answer" and a.periods == ["FY 2026–27 • Q2"]
    assert a.filters == {"Distributor": GOEL}
    assert facts(a)["total_mt"] == pytest.approx(10.8)


def test_march_and_april_fall_in_their_own_financial_years(db, seeded):
    prev = ask(db, ADMIN, "Goel minerals top customers in March 2026")
    assert facts(prev)["total_mt"] == pytest.approx(9.0)
    april = ask(db, ADMIN, "Goel minerals top customers in April 2026")
    assert facts(april)["total_mt"] == pytest.approx(3.0)
    fy25 = ask(db, ADMIN, "Goel minerals top customers in FY 2025-26")
    assert facts(fy25)["total_mt"] == pytest.approx(9.0)


def test_all_available_periods_keeps_its_own_semantics(db, seeded):
    first = ask(db, ADMIN, "Goel minerals top 5 customers")
    a = ask(db, ADMIN, "All available periods", first.spec)
    assert a.status == "answer" and a.periods == ["all available periods"]
    assert facts(a)["total_mt"] == pytest.approx(26.3)
    assert "Period: All available periods (data covers FY 2025–26 to FY 2026–27)." in a.message
    assert a.refinements == []


def test_empty_financial_year_is_no_data(db, seeded):
    a = ask(db, ADMIN, "Goel minerals top 5 customers in FY 2030-31")
    assert a.status == "no_data" and a.table is None


# ---- summary labels and customer identities ---------------------------------------------


def test_footer_row_is_not_a_customer_but_total_named_customers_are(db, seeded):
    a = ask(db, ADMIN, "Top 50 customers of Goel Minerals in Q1 FY 2026-27")
    assert FOOTER not in names(a)
    assert "Total Rubber Co" in names(a)
    assert facts(a)["group_count"] == 4


def test_reda_total_and_altek_total_sales_are_preserved(db, seeded):
    altek = ask(db, ADMIN, "Top customers of Altek International in FY 2026-27")
    assert names(altek) == ["Altek Total Sales", "Shared Buyer"]
    reda = ask(db, ADMIN, "Top customers of REDA Industrial Materials in FY 2026-27")
    assert names(reda) == ["REDA Total", "Shared Buyer"]


def test_ties_are_stable_and_blank_names_are_not_customers(db, seeded):
    qs = ChatQueryService(db, ADMIN)
    rf = ResolvedFilters(distributor_id=seeded["bajaj_chem"].id)
    rows = qs.grouped(rf, PeriodRef(fy_start=2026), QueryDimension.CUSTOMER)
    assert [r.label for r in rows] == ["Alpha One", "Beta Two"]
    total = qs.total(rf, PeriodRef(fy_start=2026))
    assert total.customer_count == 2
    assert total.quantity_mt == pytest.approx(2.5)


@pytest.mark.parametrize(
    "label,skipped",
    [
        (FOOTER, True),
        ("Totals as per 'Default' valuation", True),
        ("Total", True),
        ("Grand Total", True),
        ("Sub Total", True),
        ("Total Rubber Co", False),
        ("Totalflex Industries", False),
        ("REDA Total", False),
        ("Altek Total Sales", False),
    ],
)
def test_stock_item_parser_skips_only_confirmed_totals(label, skipped):
    assert bool(_SKIP_ROW_RE.match(label)) is skipped


def test_footer_label_helper_is_narrow():
    assert is_report_footer_label(FOOTER)
    assert not is_report_footer_label("Total Rubber Co")
    assert not is_report_footer_label("Altek Total Sales")
    assert not is_report_footer_label(None)
