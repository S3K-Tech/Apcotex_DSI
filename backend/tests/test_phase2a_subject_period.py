"""Subject period is mandatory; multi-quarter rows keep their own quarter; ADD row selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.enums import EmailProcessStatus
from app.erp_parser.documents.row_quarters import assign_row_periods
from app.exceptions import ValidationAppError
from app.services.email_subject_service import EmailSubjectService
from app.services.incremental_upload import analyse_rows, rows_for_insert, rows_for_replace
from app.utils.email_subject_parser import (
    MISSING_PERIOD_MESSAGE,
    parse_email_subject,
    subject_parse_error,
    try_parse_email_subject,
)
from app.utils.period_calendar import fy_annual_label, fy_quarter_label

Q1 = fy_quarter_label(2026, 1)
Q2 = fy_quarter_label(2026, 2)


# ---- missing / malformed period ------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    ["Reda | South | Construction", "Reda|South|Construction", "Reda - South - Construction"],
)
def test_subject_without_period_is_rejected(subject):
    with pytest.raises(ValidationAppError) as exc:
        parse_email_subject(subject)
    assert exc.value.message == MISSING_PERIOD_MESSAGE
    assert "reporting period is missing" in exc.value.message
    assert try_parse_email_subject(subject) is None
    assert subject_parse_error(subject) == MISSING_PERIOD_MESSAGE


@pytest.mark.parametrize(
    "period",
    ["Q5 FY 2026-27", "FY 2026-29", "Q1+Q2 FY 2026-28", "Sometime 2026", "KG"],
)
def test_malformed_period_is_rejected(period):
    with pytest.raises(ValidationAppError) as exc:
        parse_email_subject(f"Reda | South | Construction | {period} | MT")
    assert "Invalid period in subject" in exc.value.message


# ---- valid forms keep their existing behaviour ----------------------------------------


def test_single_quarter_subject():
    parsed = parse_email_subject("Reda | South | Construction | Q1 FY 2026-27 | KG")
    assert parsed["period"] == Q1
    assert parsed["allowed_quarters"] == ["Q1"]
    assert parsed["quarter"] == "Q1"
    assert parsed["unit"] == "KG"
    assert parsed["segment"] == "Construction"


def test_monthly_subject():
    parsed = parse_email_subject("Reda | South | Construction | APRIL 2026 | KG")
    assert parsed["period"] == Q1
    assert parsed["source_month"] == "April 2026"
    assert parsed["allowed_quarters"] == ["Q1"]


def test_annual_subject():
    parsed = parse_email_subject("Reda | South | Construction | FY 2026-27 | MT")
    assert parsed["period"] == fy_annual_label(2026)
    assert parsed["allowed_quarters"] == []


@pytest.mark.parametrize("period", ["Full Year 2026", "FY 2026", "FY 2026-27", "FY 2026-2027"])
def test_legacy_annual_forms_still_parse(period):
    parsed = parse_email_subject(f"Reda | South | Construction | {period} | MT")
    assert parsed["period"] == fy_annual_label(2026)


def test_multi_quarter_subject():
    parsed = parse_email_subject("Reda | South | Gloves | Q1+Q2 FY 2026-27 | MT")
    assert parsed["period"] == fy_annual_label(2026)
    assert parsed["allowed_quarters"] == ["Q1", "Q2"]
    assert parsed["financial_year"] == "2026-27"
    assert parsed["quarter"] is None


def test_compact_subject_still_parses():
    parsed = parse_email_subject("Reda|WEST RUBBER|Q1 FY 2026-27|KG")
    assert parsed["location"] == "West"
    assert parsed["period"] == Q1
    assert parsed["unit"] == "KG"


# ---- stale "valid" flags from before the rule are re-checked --------------------------


def _email(subject: str, **extra):
    return SimpleNamespace(
        id=7,
        subject=subject,
        subject_valid=True,
        parsed_distributor="Reda",
        parsed_location="South",
        parsed_segment="Construction",
        parsed_unit="MT",
        detected_quarter=Q1,
        process_status=EmailProcessStatus.PARSED.value,
        error_message=None,
        **extra,
    )


def test_require_valid_subject_rechecks_a_stale_valid_flag():
    with pytest.raises(ValidationAppError) as exc:
        EmailSubjectService.require_valid_subject(_email("Reda | South | Construction"))
    assert exc.value.message == MISSING_PERIOD_MESSAGE


def test_apply_to_email_reparses_a_stale_valid_flag():
    email = _email("Reda | South | Construction")
    svc = EmailSubjectService(MagicMock())
    svc.audit = MagicMock()
    assert svc.apply_to_email(email, skip_if_valid=True) is False
    assert email.subject_valid is False
    assert email.process_status == EmailProcessStatus.INVALID_SUBJECT.value
    assert email.error_message == MISSING_PERIOD_MESSAGE


def test_apply_to_email_takes_period_from_subject_not_workbook():
    email = _email("Reda | South | Construction | Q2 FY 2026-27 | MT", )
    email.subject_valid = False
    email.detected_quarter = Q1  # e.g. a workbook-detected value
    svc = EmailSubjectService(MagicMock())
    svc.audit = MagicMock()
    assert svc.apply_to_email(email) is True
    assert email.detected_quarter == Q2


# ---- Q1+Q2: each row keeps its own quarter ---------------------------------------------


def test_q1_q2_rows_get_their_own_quarter_and_out_of_range_rows_are_held():
    parsed = parse_email_subject("Reda | South | Gloves | Q1+Q2 FY 2026-27 | MT")
    rows = [
        {"customer_name": "A", "product": "P", "sales_quantity": 1, "source_month": "May 2026"},
        {"customer_name": "B", "product": "P", "sales_quantity": 2, "source_month": "August 2026"},
        {"customer_name": "C", "product": "P", "sales_quantity": 3, "transaction_date": "2026-06-15"},
        {"customer_name": "D", "product": "P", "sales_quantity": 4, "period": Q2},
        {"customer_name": "E", "product": "P", "sales_quantity": 5, "source_month": "October 2026"},
        {"customer_name": "F", "product": "P", "sales_quantity": 6, "source_month": "May 2025"},
        {"customer_name": "G", "product": "P", "sales_quantity": 7},
    ]
    handled, notes = assign_row_periods(rows, parsed)
    assert handled is True
    by_customer = {r["customer_name"]: r for r in rows}
    assert by_customer["A"]["period"] == Q1
    assert by_customer["B"]["period"] == Q2
    assert by_customer["C"]["period"] == Q1
    assert by_customer["D"]["period"] == Q2
    for held in ("E", "F", "G"):
        assert by_customer[held].get("human_review") is True
    for kept in ("A", "B", "C", "D"):
        assert not by_customer[kept].get("human_review")
    assert len(notes) == 3
    # The combined subject label is never stamped onto rows.
    assert all(r.get("period") != fy_annual_label(2026) for r in rows)


# ---- ADD THIS row selection -------------------------------------------------------------


def _row(customer, qty, month="April 2026"):
    return {
        "customer_name": customer,
        "product": "TX 400",
        "source_month": month,
        "period": Q1,
        "sales_quantity": qty,
        "segment": "Construction",
        "location": "South",
    }


def _existing(record_id, customer, qty, month="April 2026"):
    return {
        "id": record_id,
        "customer_name": customer,
        "product": "TX 400",
        "source_month": month,
        "period": Q1,
        "quantity": qty,
        "segment": "Construction",
        "location": "South",
    }


def test_add_inserts_new_and_modified_rows_but_never_exact_copies():
    incoming = [_row("Exact", 10), _row("Changed", 60), _row("Brand New", 5)]
    existing = [_existing(1, "Exact", 10), _existing(2, "Changed", 50)]
    plan = analyse_rows(incoming, existing, location="South", segment="Construction")
    assert (plan["exact_count"], plan["modified_count"], plan["new_count"]) == (1, 1, 1)

    added = rows_for_insert(incoming, plan, None, confirm_add=True)
    assert sorted(r["customer_name"] for r in added) == ["Brand New", "Changed"]
    assert rows_for_replace(plan, None, confirm_add=True) == []


def test_without_add_modified_rows_need_a_per_row_choice():
    incoming = [_row("Changed", 60)]
    plan = analyse_rows(incoming, [_existing(2, "Changed", 50)], location="South", segment="Construction")
    assert rows_for_insert(incoming, plan, None, confirm_add=False) == []
    chosen = [{"row_index": 0, "action": "add"}]
    assert len(rows_for_insert(incoming, plan, chosen, confirm_add=False)) == 1
