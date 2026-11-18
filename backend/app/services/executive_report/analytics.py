"""Deterministic analytics snapshot behind an executive report.

Every number here is computed in SQL with the same filter, RBAC scope and MT
expression the Visualizations dashboard uses. The LLM only interprets it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.models.distributor import Distributor
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.repositories.sales_record_repository import company_expr, mt_quantity_expr
from app.services.analytics_aggregation import AnalyticsFilter
from app.services.executive_report.filters import (
    comparison_periods,
    describe_filters,
    period_label,
    report_kind,
    report_title,
)
from app.services.product_standardization import normalize_product_key, prefer_display_name
from app.services.sales_insights_service import (
    SalesInsightsService,
    _customer_identity_expr,
    _month_from_submission,
    _to_float,
)
from app.utils.quantity import format_mt, round_mt

UNSPECIFIED_CUSTOMER = "(Customer not specified)"
UNSPECIFIED_DISTRIBUTOR = "(Distributor not specified)"
MOVERS_LIMIT = 8


def _pct(part: float, whole: float) -> Optional[float]:
    if not whole:
        return None
    return round(part / whole * 100.0, 1)


def _scope_parts(filters: Dict[str, Any], allowed_segments, allowed_companies):
    filt = AnalyticsFilter(
        financial_year=filters.get("fiscal_year_start"),
        period=filters.get("period"),
        distributor_id=filters.get("distributor_id"),
        distributor=filters.get("distributor"),
        customer=filters.get("customer"),
        product=filters.get("product"),
        location=filters.get("location"),
        segment=filters.get("segment"),
        start_month=filters.get("start_month"),
        end_month=filters.get("end_month"),
    )
    scope_kw = dict(
        customer=filters.get("customer"),
        product=filters.get("product"),
        location=filters.get("location"),
        segment=filters.get("segment"),
        search=None,
        allowed_segments=allowed_segments,
        allowed_companies=allowed_companies,
    )
    return filt, scope_kw


def _grouped(svc: SalesInsightsService, filt, scope_kw, column) -> List[Any]:
    query = (
        select(
            column,
            func.coalesce(func.sum(mt_quantity_expr()), 0),
            func.count(SalesRecord.id),
        )
        .select_from(SalesRecord)
        .join(Report, Report.id == SalesRecord.report_id)
        .join(Distributor, Distributor.id == SalesRecord.distributor_id)
        .group_by(column)
    )
    return svc.db.execute(svc._with_filter(query, filt, **scope_kw)).all()


def _ranked(buckets: Dict[str, Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    rows = sorted(buckets.values(), key=lambda r: (-r["mt"], r["name"].casefold()))
    out = []
    for rank, row in enumerate(rows, start=1):
        mt = round_mt(row["mt"])
        out.append(
            {
                "rank": rank,
                "key": row["key"],
                "name": row["name"],
                "mt": mt,
                "mt_display": format_mt(mt),
                "share_pct": _pct(mt, total),
                "records": int(row["records"]),
            }
        )
    return out


def breakdowns(
    svc: SalesInsightsService,
    filters: Dict[str, Any],
    *,
    allowed_segments: Optional[List[str]],
    allowed_companies: Optional[List[str]],
) -> Dict[str, Any]:
    """Totals plus full product / customer / distributor rankings for one filter set."""
    filt, scope_kw = _scope_parts(filters, allowed_segments, allowed_companies)

    products: Dict[str, Dict[str, Any]] = {}
    for name, qty, count in _grouped(svc, filt, scope_kw, SalesRecord.product):
        raw = " ".join(str(name or "").split())
        key = normalize_product_key(raw) or "__blank__"
        slot = products.setdefault(key, {"key": key, "name": "", "mt": 0.0, "records": 0})
        if raw:
            slot["name"] = prefer_display_name(slot["name"] or None, raw)
        elif not slot["name"]:
            slot["name"] = "(Product not specified)"
        slot["mt"] += _to_float(qty)
        slot["records"] += int(count or 0)

    identity = _customer_identity_expr()
    customer_q = (
        select(
            identity,
            func.min(SalesRecord.customer_name),
            func.coalesce(func.sum(mt_quantity_expr()), 0),
            func.count(SalesRecord.id),
        )
        .select_from(SalesRecord)
        .join(Report, Report.id == SalesRecord.report_id)
        .join(Distributor, Distributor.id == SalesRecord.distributor_id)
        .group_by(identity)
    )
    customers: Dict[str, Dict[str, Any]] = {}
    for key, display, qty, count in svc.db.execute(svc._with_filter(customer_q, filt, **scope_kw)).all():
        k = key or "__blank__"
        name = " ".join(str(display or "").split()) or UNSPECIFIED_CUSTOMER
        customers[k] = {"key": k, "name": name, "mt": _to_float(qty), "records": int(count or 0)}

    company = company_expr()
    distributor_q = (
        select(
            company,
            func.coalesce(func.sum(mt_quantity_expr()), 0),
            func.count(SalesRecord.id),
        )
        .select_from(SalesRecord)
        .join(Report, Report.id == SalesRecord.report_id)
        .join(Distributor, Distributor.id == SalesRecord.distributor_id)
        .group_by(company)
    )
    distributors: Dict[str, Dict[str, Any]] = {}
    for name, qty, count in svc.db.execute(svc._with_filter(distributor_q, filt, **scope_kw)).all():
        display = " ".join(str(name or "").split()) or UNSPECIFIED_DISTRIBUTOR
        k = display.casefold()
        slot = distributors.setdefault(k, {"key": k, "name": display, "mt": 0.0, "records": 0})
        slot["mt"] += _to_float(qty)
        slot["records"] += int(count or 0)

    total = round_mt(sum(p["mt"] for p in products.values()))
    records = sum(int(p["records"]) for p in products.values())
    return {
        "total_mt": total,
        "total_mt_display": format_mt(total),
        "record_count": records,
        "products": _ranked(products, total),
        "customers": _ranked(customers, total),
        "distributors": _ranked(distributors, total),
    }


def _concentration(rows: List[Dict[str, Any]], total: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {"count": len(rows)}
    for n in (1, 3, 5):
        if len(rows) >= n:
            out[f"top{n}_share_pct"] = _pct(sum(r["mt"] for r in rows[:n]), total)
    return out


def _movers(current: List[Dict[str, Any]], previous: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Changes for names present in both periods, plus names present in only one."""
    before = {r["key"]: r for r in previous}
    now = {r["key"]: r for r in current}
    changes = []
    for key, row in now.items():
        prev = before.get(key)
        if prev is None:
            continue
        delta = round_mt(row["mt"] - prev["mt"])
        changes.append(
            {
                "name": row["name"],
                "current_mt": row["mt"],
                "previous_mt": prev["mt"],
                "change_mt": delta,
                "change_pct": _pct(delta, prev["mt"]) if prev["mt"] > 0 else None,
            }
        )
    increases = sorted((c for c in changes if c["change_mt"] > 0), key=lambda c: -c["change_mt"])
    decreases = sorted((c for c in changes if c["change_mt"] < 0), key=lambda c: c["change_mt"])
    new = [
        {"name": r["name"], "current_mt": r["mt"]}
        for r in current
        if r["key"] not in before
    ]
    lapsed = [
        {"name": r["name"], "previous_mt": r["mt"]}
        for r in previous
        if r["key"] not in now
    ]
    return {
        "increases": increases[:MOVERS_LIMIT],
        "decreases": decreases[:MOVERS_LIMIT],
        "only_in_current_period": new[:MOVERS_LIMIT],
        "only_in_current_period_count": len(new),
        "no_sales_in_current_period": lapsed[:MOVERS_LIMIT],
        "no_sales_in_current_period_count": len(lapsed),
    }


def _month_granularity(
    svc: SalesInsightsService, filters: Dict[str, Any], allowed_segments, allowed_companies
) -> Dict[str, Any]:
    """Share of quantity whose month is recorded. The rest is spread evenly on the chart."""
    filt, scope_kw = _scope_parts(filters, allowed_segments, allowed_companies)
    query = (
        select(
            SalesRecord.source_month,
            Report.name,
            SalesRecord.period,
            Report.reporting_month,
            func.coalesce(func.sum(mt_quantity_expr()), 0),
        )
        .select_from(SalesRecord)
        .join(Report, Report.id == SalesRecord.report_id)
        .join(Distributor, Distributor.id == SalesRecord.distributor_id)
        .group_by(SalesRecord.source_month, Report.name, SalesRecord.period, Report.reporting_month)
    )
    exact = total = 0.0
    for source_month, report_name, period, reporting_month, qty in svc.db.execute(
        svc._with_filter(query, filt, **scope_kw)
    ).all():
        amount = abs(_to_float(qty))
        total += amount
        if _month_from_submission(source_month, report_name, period, reporting_month):
            exact += amount
    share = _pct(exact, total) if total else None
    if share is None:
        basis = "none"
    elif share >= 99.9:
        basis = "recorded_months"
    elif share <= 0.1:
        basis = "estimated_from_quarterly_totals"
    else:
        basis = "partly_estimated"
    return {"monthly_basis": basis, "share_with_recorded_month_pct": share}


def build_snapshot(
    db,
    filters: Dict[str, Any],
    *,
    allowed_segments: Optional[List[str]],
    allowed_companies: Optional[List[str]],
) -> Dict[str, Any]:
    svc = SalesInsightsService(db)
    dashboard = svc.sales_insights(
        **filters,
        page=1,
        page_size=1,
        allowed_segments=allowed_segments,
        allowed_companies=allowed_companies,
    )
    current = breakdowns(
        svc, filters, allowed_segments=allowed_segments, allowed_companies=allowed_companies
    )
    total = float(dashboard["kpis"]["total_sales_mt"])

    comparisons = []
    for comp in comparison_periods(filters):
        other = breakdowns(
            svc,
            comp["filters"],
            allowed_segments=allowed_segments,
            allowed_companies=allowed_companies,
        )
        available = other["record_count"] > 0
        entry: Dict[str, Any] = {
            "key": comp["key"],
            "description": comp["description"],
            "period": comp["label"],
            "available": available,
        }
        if available:
            delta = round_mt(total - other["total_mt"])
            entry.update(
                {
                    "total_mt": other["total_mt"],
                    "total_mt_display": other["total_mt_display"],
                    "change_mt": delta,
                    "change_pct": _pct(delta, other["total_mt"]) if other["total_mt"] > 0 else None,
                    "customers_with_sales": len(other["customers"]),
                    "products_with_sales": len(other["products"]),
                    "distributors_with_sales": len(other["distributors"]),
                    "products": _movers(current["products"], other["products"]),
                    "customers": _movers(current["customers"], other["customers"]),
                    "distributors": _movers(current["distributors"], other["distributors"]),
                }
            )
        comparisons.append(entry)

    monthly = [
        {"month": p.get("tooltip") or p.get("month"), "mt": p["qty"], "mt_display": format_mt(p["qty"])}
        for p in dashboard.get("monthly_trend") or []
    ]
    distributor_chart = [{"distributor": r["name"], "qty": r["mt"]} for r in current["distributors"]]

    kpis = dashboard["kpis"]
    return {
        "period": period_label(filters),
        "report_title": report_title(filters),
        "report_kind": report_kind(filters),
        "filters": filters,
        "filter_lines": describe_filters(filters),
        "unit": "MT (metric tonnes of sales quantity; no monetary values are available)",
        "kpis": {
            "total_sales_mt": kpis["total_sales_mt"],
            "total_sales_mt_display": kpis["total_sales_mt_display"],
            "total_customers": kpis["total_customers"],
            "total_products": kpis["total_products"],
            "active_distributors": len(current["distributors"]),
            "average_quarterly_sales_mt": kpis.get("average_quarterly_sales_mt"),
            "average_quarterly_sales_mt_display": kpis.get("average_quarterly_sales_mt_display"),
            "record_count": dashboard.get("record_count", 0),
        },
        "charts": {
            "quarterly_totals": dashboard.get("quarterly_totals") or [],
            "monthly_trend": dashboard.get("monthly_trend") or [],
            "top_customers": dashboard.get("top_customers") or [],
            "product_contribution": dashboard.get("product_contribution") or [],
            "distributor_contribution": distributor_chart,
        },
        "monthly": monthly,
        "monthly_quality": _month_granularity(svc, filters, allowed_segments, allowed_companies),
        "products": current["products"],
        "customers": current["customers"],
        "distributors": current["distributors"],
        "concentration": {
            "customers": _concentration(current["customers"], total),
            "products": _concentration(current["products"], total),
            "distributors": _concentration(current["distributors"], total),
        },
        "comparisons": comparisons,
    }
