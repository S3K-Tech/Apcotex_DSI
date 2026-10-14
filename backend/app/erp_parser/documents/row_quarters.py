"""Assign each sales row its own quarter. The subject only limits the allowed range."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.utils.period_calendar import (
    fy_quarter_label,
    fy_short_display,
    fy_start_for_calendar_month,
    month_label,
    parse_month_label,
    parse_quarter_label,
    quarter_of_month,
)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_TOKEN_RE = re.compile(
    r"(\d{1,2})[-/\s]([A-Za-z]{3}|\d{1,2})[-/\s](\d{2,4})"
)


def allowed_quarter_list(parsed: Optional[Dict[str, Any]]) -> List[str]:
    raw = (parsed or {}).get("allowed_quarters")
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip().upper() for item in raw if str(item).strip()]
    else:
        parts = [part.strip().upper() for part in str(raw or "").split(",") if part.strip()]
    found = []
    for part in parts:
        token = part if part.startswith("Q") else f"Q{part}" if part in {"1", "2", "3", "4"} else part
        if token in {"Q1", "Q2", "Q3", "Q4"} and token not in found:
            found.append(token)
    if found:
        return found
    single = str((parsed or {}).get("quarter") or "").strip().upper()
    return [single] if single else []


def subject_fy_start(parsed: Optional[Dict[str, Any]]) -> Optional[int]:
    """FY start year named in the subject (``FY 2026-27`` / ``2026-27`` → 2026)."""
    for key in ("period", "financial_year"):
        raw = str((parsed or {}).get(key) or "").strip()
        if not raw:
            continue
        spec = parse_quarter_label(raw)
        if spec and spec.year is not None:
            return int(spec.year)
        match = re.search(r"(19|20)\d{2}", raw)
        if match:
            return int(match.group(0))
    return None


def subject_fiscal_year_start(subject: Optional[str]) -> Optional[int]:
    """FY start named in an email subject; None when the subject names no period."""
    if not str(subject or "").strip():
        return None
    from app.utils.email_subject_parser import try_parse_email_subject

    parsed = try_parse_email_subject(subject) or {}
    if not (parsed.get("period") or parsed.get("financial_year")):
        return None
    return subject_fy_start(parsed)


def assign_row_periods(
    rows: Sequence[Dict[str, Any]],
    parsed: Optional[Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    """
    Give every row the FY quarter of its own month. The subject only limits the range.

    The subject FY and quarter list are authoritative: a row from another FY or a quarter
    outside the list is flagged ``human_review`` and never re-labelled. A row with no
    month is stamped with the subject quarter only when the subject names exactly one
    quarter; in a multi-quarter submission it is flagged, because the combined subject
    period (``FY 2026-27``) is not a reporting bucket.

    Returns (handled, warnings). Subjects without quarters (full FY) are not handled.
    """
    allowed = allowed_quarter_list(parsed)
    fy_start = subject_fy_start(parsed)
    if not allowed or fy_start is None:
        return False, []
    scope = f"{'+'.join(allowed)} {fy_short_display(fy_start)}"
    subject_month = str((parsed or {}).get("source_month") or "").strip()
    warnings: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if len(allowed) == 1 and subject_month and not str(row.get("source_month") or "").strip():
            if _find_date(row) is None:
                row["source_month"] = subject_month
        period = _period_from_row(row)
        if not period and len(allowed) == 1:
            period = fy_quarter_label(fy_start, int(allowed[0][1]))
        note = ""
        if not period:
            note = f"Row has no month or quarter; cannot place it inside {scope}"
        else:
            row["period"] = period
            row["reporting_quarter"] = period
            spec = parse_quarter_label(period)
            if spec is None or spec.year != fy_start:
                note = f"Row period {period} is outside the subject financial year {fy_short_display(fy_start)}"
            elif f"Q{spec.quarter}" not in allowed:
                note = f"Row quarter Q{spec.quarter} is outside the subject range {scope}"
        if note:
            row["validation_warning"] = note
            row["human_review"] = True
            warnings.append(note)
            continue
        row.pop("human_review", None)
    return True, warnings


def quarter_split(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        quarter = _quarter_token((row or {}).get("period"))
        if not quarter:
            continue
        counts[quarter] = counts.get(quarter, 0) + 1
    return counts


def _period_from_row(row: Dict[str, Any]) -> Optional[str]:
    """Quarter of the row's own month/date; a quarter label on the row is the fallback."""
    month_hit = parse_month_label(str(row.get("source_month") or ""))
    if month_hit:
        month, year = month_hit
    else:
        parsed_date = _find_date(row)
        if parsed_date is not None:
            month, year = parsed_date.month, parsed_date.year
            row["source_month"] = month_label(month, year)
            row["transaction_date"] = parsed_date.isoformat()
        else:
            for key in ("period", "reporting_quarter"):
                spec = parse_quarter_label(str(row.get(key) or ""))
                if spec and spec.kind == "quarter" and spec.year is not None and spec.quarter:
                    return fy_quarter_label(spec.year, spec.quarter)
            return None
    start = fy_start_for_calendar_month(month, year)
    return fy_quarter_label(start, quarter_of_month(month))


def _find_date(row: Dict[str, Any]) -> Optional[date]:
    for key in (
        "transaction_date",
        "date",
        "invoice_date",
        "voucher_date",
        "bill_date",
    ):
        found = _coerce_date(row.get(key))
        if found:
            return found
    return None


def _coerce_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    month_hit = parse_month_label(text)
    if month_hit:
        month, year = month_hit
        return date(year, month, 1)
    iso = _ISO_RE.match(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _TOKEN_RE.search(text)
    if not match:
        return None
    first = int(match.group(1))
    middle = match.group(2)
    year = int(match.group(3))
    if year < 100:
        year += 2000
    if middle.isdigit():
        month = int(middle)
        day = first
        if month > 12 and first <= 12:
            day, month = month, first
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            return None
    else:
        month = _MONTHS.get(middle[:3].lower())
        day = first
        if month is None:
            return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _quarter_token(value: Any) -> str:
    spec = parse_quarter_label(str(value or ""))
    if spec and spec.kind == "quarter" and spec.quarter:
        return f"Q{spec.quarter}"
    text = str(value or "").upper()
    match = re.search(r"\bQ([1-4])\b", text)
    return f"Q{match.group(1)}" if match else ""
