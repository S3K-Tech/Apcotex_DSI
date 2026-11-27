"""Unit tests for Chat with Data Excel export."""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from app.schemas.chat_data import (
    ChatChart,
    ChatChartPoint,
    ChatExportRequest,
    ChatFact,
    ChatTable,
    ChatTableColumn,
)
from app.services.chat_data.excel_export import build_chat_excel, build_export_filename


def test_build_chat_excel_with_table_and_facts():
    req = ChatExportRequest(
        question="distribution of distributors for Q1 FY 26-27",
        message="Here is the distributor breakdown for Q1 FY 2026-27.",
        periods=["FY 2026-27 • Q1"],
        filters={"Segment": "Paper"},
        facts=[ChatFact(key="total_mt", label="Total Sales", value=125.5, display="125.500 MT")],
        table=ChatTable(
            columns=[
                ChatTableColumn(key="rank", label="#", numeric=True),
                ChatTableColumn(key="name", label="Distributor", numeric=False),
                ChatTableColumn(key="value", label="Sales (MT)", numeric=True),
                ChatTableColumn(key="share_pct", label="Share", numeric=True),
            ],
            rows=[
                {"rank": 1, "name": "Alpha Dist", "value": 80.5, "share_pct": 64.1},
                {"rank": 2, "name": "Beta Dist", "value": 45.0, "share_pct": 35.9},
            ],
        ),
    )
    raw = build_chat_excel(req)
    wb = load_workbook(BytesIO(raw))
    ws = wb.active
    values = [list(r) for r in ws.iter_rows(values_only=True)]
    flat_text = " ".join(str(v) for row in values for v in row if v is not None)
    assert "Alpha Dist" in flat_text
    assert "Beta Dist" in flat_text
    assert "FY 2026-27 • Q1" in flat_text
    assert "125.500 MT" in flat_text


def test_build_chat_excel_chart_fallback():
    req = ChatExportRequest(
        question="pie chart by region",
        message="Sales by region.",
        chart=ChatChart(
            type="pie",
            title="By Region",
            unit="MT",
            points=[ChatChartPoint(name="West", value=10.25)],
        ),
    )
    raw = build_chat_excel(req)
    wb = load_workbook(BytesIO(raw))
    ws = wb.active
    flat_text = " ".join(
        str(v) for row in ws.iter_rows(values_only=True) for v in row if v is not None
    )
    assert "West" in flat_text
    assert "10.25" in flat_text


def test_build_export_filename_sanitizes():
    name = build_export_filename("distribution of distributors for Q1 FY 26-27?")
    assert name.startswith("chat_data_distribution_of_distributors_")
    assert name.endswith(".xlsx")
