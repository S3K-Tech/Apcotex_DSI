"""Chat follow-up region continuity, list-all distributors, and chart requests."""

from __future__ import annotations

from app.schemas.chat_data import AwaitingField, QueryDimension, QueryIntent
from app.services.chat_data.answer_builder import build_chart
from app.services.chat_data.intent_service import RuleParser, _chart_type
from app.services.chat_data.query_service import GroupRow


def test_chart_type_detection():
    assert _chart_type("show a pie chart of products") == "pie"
    assert _chart_type("bar graph for distributors") == "bar"
    assert _chart_type("line chart monthly trend") == "line"
    assert _chart_type("make a chart of customers") == "pie"
    assert _chart_type("top 5 customers") is None


def test_list_all_distributors_distribution_is_list_not_top10():
    p = RuleParser()
    spec = p.parse("show me all distributors data distribution in FY 2026-27")
    assert spec.intent == QueryIntent.LIST
    assert spec.dimension == QueryDimension.DISTRIBUTOR


def test_distribution_without_top_n_is_full_list():
    p = RuleParser()
    spec = p.parse("distributors data distribution FY 2025-26")
    assert spec.intent == QueryIntent.LIST
    assert spec.dimension == QueryDimension.DISTRIBUTOR


def test_fresh_follow_up_asks_region_instead_of_keeping_india_west():
    p = RuleParser()
    first = p.parse("Top 5 customers in India West in FY 2026-27")
    assert first.filters.location == "India West"
    follow = p.parse("top 10 distributors in FY 2026-27", first)
    assert follow.awaiting == AwaitingField.LOCATION
    assert follow.filters.location is None
    assert "All regions" in (follow.clarification_question or "") or "all regions" in (
        follow.clarification_question or ""
    ).casefold()


def test_period_follow_up_keeps_region():
    p = RuleParser()
    first = p.parse("Top 5 customers in India West in FY 2026-27")
    follow = p.parse("What about Q2 FY 2026-27?", first)
    assert follow.awaiting is None
    assert follow.filters.location == "India West"
    assert follow.periods and follow.periods[0].quarter == 2


def test_continuity_phrase_keeps_region():
    p = RuleParser()
    first = p.parse("Top 5 customers in India West in FY 2026-27")
    follow = p.parse("now top 10 distributors for that same region in FY 2026-27", first)
    assert follow.awaiting is None
    assert follow.filters.location == "India West"
    assert follow.dimension == QueryDimension.DISTRIBUTOR


def test_location_all_regions_answer_clears_filter():
    p = RuleParser()
    first = p.parse("Top 5 customers in India West in FY 2026-27")
    pending = p.parse("top 10 distributors in FY 2026-27", first)
    assert pending.awaiting == AwaitingField.LOCATION
    cleared = p.parse("All regions", pending)
    assert cleared.awaiting is None
    assert cleared.filters.location is None
    assert cleared.intent == QueryIntent.RANK
    assert cleared.dimension == QueryDimension.DISTRIBUTOR


def test_pie_chart_request_sets_chart_type():
    p = RuleParser()
    spec = p.parse("pie chart of products in FY 2026-27")
    assert spec.chart_type == "pie"
    assert spec.dimension == QueryDimension.PRODUCT
    assert spec.intent in (QueryIntent.LIST, QueryIntent.BREAKDOWN, QueryIntent.RANK)


def test_build_chart_from_rows():
    rows = [
        GroupRow(key="a", label="Alpha", quantity_mt=10.0),
        GroupRow(key="b", label="Beta", quantity_mt=5.0),
    ]
    chart = build_chart("pie", title="Test", rows=rows, total=15.0)
    assert chart is not None
    assert chart.type == "pie"
    assert len(chart.points) == 2
    assert chart.points[0].name == "Alpha"
