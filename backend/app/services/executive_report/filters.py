"""Report filter normalization, identity key, period labels and comparison periods."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from app.utils.period_calendar import fy_short

FILTER_FIELDS = (
    "distributor_id",
    "distributor",
    "customer",
    "product",
    "location",
    "segment",
    "fiscal_year_start",
    "period",
    "start_month",
    "end_month",
)

QUARTER_PERIODS = {"q1": 1, "q2": 2, "q3": 3, "q4": 4}
_FY_PERIODS = {"full_year", *QUARTER_PERIODS}
_ROLLING_LABELS = {
    "last_3_months": "Last 3 Months",
    "last_6_months": "Last 6 Months",
    "last_12_months": "Last 12 Months",
}
_KNOWN_PERIODS = _FY_PERIODS | set(_ROLLING_LABELS) | {"custom"}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() == "all":
        return None
    return text


def _int(value: Any) -> Optional[int]:
    if value in (None, "", "All", "all"):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_filters(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Same meaning as the Visualizations query; fields that do not apply are dropped."""
    period = (_text(raw.get("period")) or "full_year").lower().replace(" ", "_").replace("-", "_")
    if period not in _KNOWN_PERIODS:
        period = "full_year"
    fy = _int(raw.get("fiscal_year_start")) if period in _FY_PERIODS else None
    if period in _FY_PERIODS and fy is None:
        from app.services.sales_insights_service import _fallback_fy_start

        fy = _fallback_fy_start()
    custom = period == "custom"
    return {
        "distributor_id": _int(raw.get("distributor_id")),
        "distributor": _text(raw.get("distributor")),
        "customer": _text(raw.get("customer")),
        "product": _text(raw.get("product")),
        "location": _text(raw.get("location")),
        "segment": _text(raw.get("segment")),
        "fiscal_year_start": fy,
        "period": period,
        "start_month": _text(raw.get("start_month")) if custom else None,
        "end_month": _text(raw.get("end_month")) if custom else None,
    }


def filter_key(filters: Dict[str, Any]) -> str:
    canonical = {
        k: (v.casefold() if isinstance(v, str) else v)
        for k, v in normalize_filters(filters).items()
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def period_label(filters: Dict[str, Any]) -> str:
    """``Q1 FY 2026-27``, ``FY 2026-27``, ``Last 3 Months`` or ``2026-04 to 2026-06``."""
    period = filters.get("period") or "full_year"
    fy = filters.get("fiscal_year_start")
    if period in QUARTER_PERIODS and fy:
        return f"Q{QUARTER_PERIODS[period]} {fy_short(fy)}"
    if period == "full_year" and fy:
        return fy_short(fy)
    if period in _ROLLING_LABELS:
        return _ROLLING_LABELS[period]
    if period == "custom":
        return f"{filters.get('start_month') or '?'} to {filters.get('end_month') or '?'}"
    return str(period)


def report_kind(filters: Dict[str, Any]) -> str:
    """Short kind for titles/filenames based on the Visualizations period filter."""
    period = filters.get("period") or "full_year"
    if period in QUARTER_PERIODS:
        return "Quarterly"
    if period == "full_year":
        return "Annual"
    if period in _ROLLING_LABELS:
        return "Period"
    if period == "custom":
        return "Custom"
    return "Secondary"


def report_title(filters: Dict[str, Any]) -> str:
    """Main PDF/UI title driven by the selected filters (never always 'Quarterly')."""
    kind = report_kind(filters)
    if kind == "Quarterly":
        return "Quarterly Secondary Sales Report"
    if kind == "Annual":
        return "Annual Secondary Sales Report"
    if kind == "Period":
        return "Secondary Sales Report"
    if kind == "Custom":
        return "Custom Secondary Sales Report"
    return "Secondary Sales Report"


def report_filename_stem(filters: Dict[str, Any]) -> str:
    """Filesystem-safe stem, e.g. APCOTEX_Annual_Secondary_Sales_FY_2026_27."""
    kind = report_kind(filters)
    period = period_label(filters)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", period).strip("_")
    if kind == "Quarterly":
        return f"APCOTEX_Quarterly_Secondary_Sales_{slug}"
    if kind == "Annual":
        return f"APCOTEX_Annual_Secondary_Sales_{slug}"
    if kind == "Period":
        return f"APCOTEX_Secondary_Sales_{slug}"
    return f"APCOTEX_Secondary_Sales_{slug}"


def describe_filters(filters: Dict[str, Any]) -> List[Dict[str, str]]:
    """Human-readable filter lines for the report header and email."""
    rows = [{"label": "Reporting Period", "value": period_label(filters)}]
    labels = (
        ("distributor", "Distributor"),
        ("segment", "Segment"),
        ("location", "Location"),
        ("customer", "Customer"),
        ("product", "Product"),
    )
    for key, label in labels:
        rows.append({"label": label, "value": filters.get(key) or "All"})
    return rows


def comparison_periods(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Comparable earlier periods. Rolling and custom ranges have none."""
    period = filters.get("period")
    fy = filters.get("fiscal_year_start")
    if not fy:
        return []
    out: List[Dict[str, Any]] = []
    if period in QUARTER_PERIODS:
        quarter = QUARTER_PERIODS[period]
        prev_fy, prev_q = (fy, quarter - 1) if quarter > 1 else (fy - 1, 4)
        out.append(
            {
                "key": "previous_quarter",
                "description": "Previous quarter",
                "filters": {**filters, "fiscal_year_start": prev_fy, "period": f"q{prev_q}"},
            }
        )
        out.append(
            {
                "key": "same_quarter_last_year",
                "description": "Same quarter of the previous financial year",
                "filters": {**filters, "fiscal_year_start": fy - 1},
            }
        )
    elif period == "full_year":
        out.append(
            {
                "key": "previous_year",
                "description": "Previous financial year",
                "filters": {**filters, "fiscal_year_start": fy - 1},
            }
        )
    for item in out:
        item["label"] = period_label(item["filters"])
    return out
