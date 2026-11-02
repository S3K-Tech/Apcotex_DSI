"""Financial-year period helpers for Chat with Data (April–March, explicit FY only)."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from sqlalchemy import func, or_

from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.schemas.chat_data import PeriodRef
from app.services.analytics_aggregation import AnalyticsFilter
from app.utils.period_calendar import (
    calendar_year_for_fy_month,
    fy_short_display,
    month_label,
    months_for_fy_quarter,
    parse_month_label,
)


def period_label(period: PeriodRef) -> str:
    if period.month is not None:
        year = calendar_year_for_fy_month(period.fy_start, period.month)
        return f"{month_label(period.month, year)} ({fy_short_display(period.fy_start)})"
    if period.quarter is not None:
        return f"{fy_short_display(period.fy_start)} • Q{period.quarter}"
    return fy_short_display(period.fy_start)


def apply_period(query, period: Optional[PeriodRef]):
    """Restrict to one explicit period. ``None`` leaves every period in scope."""
    if period is None:
        return query
    if period.month is not None:
        label = month_label(period.month, calendar_year_for_fy_month(period.fy_start, period.month))
        keys = [label]
        return query.where(
            or_(
                func.trim(func.coalesce(SalesRecord.period, "")).in_(keys),
                func.trim(func.coalesce(SalesRecord.source_month, "")).in_(keys),
                func.trim(func.coalesce(Report.reporting_month, "")).in_(keys),
            )
        )
    filt = AnalyticsFilter(
        financial_year=period.fy_start,
        period=f"q{period.quarter}" if period.quarter else "full_year",
    )
    return filt.restrict(query)


def actual_month_of_row(
    source_month: Optional[str], reporting_month: Optional[str], row_period: Optional[str]
) -> Optional[Tuple[int, int]]:
    """(year, month) only when the row itself carries a calendar month. Never estimated."""
    for raw in (source_month, reporting_month, row_period):
        parsed = parse_month_label(str(raw or "").strip())
        if parsed:
            month, year = parsed
            return year, month
    return None


def period_statement(period: Optional[PeriodRef], coverage: Sequence[int] = ()) -> str:
    """One explicit sentence naming the period and its calendar range.

    ``coverage`` lists the financial years with data (used for all-periods answers).
    """
    if period is None:
        years = sorted(set(coverage))
        if not years:
            return "Period: All available periods."
        if len(years) == 1:
            return f"Period: All available periods (data covers {fy_short_display(years[0])})."
        return (
            "Period: All available periods "
            f"(data covers {fy_short_display(years[0])} to {fy_short_display(years[-1])})."
        )
    if period.month is not None:
        year = calendar_year_for_fy_month(period.fy_start, period.month)
        return f"Period: {month_label(period.month, year)} ({fy_short_display(period.fy_start)})."
    if period.quarter is not None:
        months = months_for_fy_quarter(period.fy_start, period.quarter)
        return (
            f"Period: Q{period.quarter} {fy_short_display(period.fy_start)} "
            f"({months[0]} – {months[-1]})."
        )
    return (
        f"Period: Full financial year {fy_short_display(period.fy_start)} "
        f"(April {period.fy_start} – March {period.fy_start + 1})."
    )


def quarter_label(fy_start: int, quarter: int) -> str:
    return f"{fy_short_display(fy_start)} • Q{quarter}"

