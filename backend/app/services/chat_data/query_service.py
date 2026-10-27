"""Deterministic, parameterized analytics queries for Chat with Data.

Every query starts from the Analytics base (sales → report → distributor,
soft-delete filters, RBAC scope) and uses ``mt_quantity_expr`` for MT.
No SQL text is built from user or model input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, distinct, func, not_, or_, select, text
from sqlalchemy.orm import Session

from app.models.distributor import Distributor
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.repositories.sales_record_repository import (
    _KG_UNIT_LABELS,
    _MT_UNIT_LABELS,
    company_expr,
    mt_quantity_expr,
)
from app.schemas.chat_data import PeriodRef, QueryDimension
from app.services.chat_data.periods import actual_month_of_row, apply_period, quarter_label
from app.services.chat_data.scope import ChatScope
from app.services.product_standardization import (
    canonical_products,
    expand_product_filter,
    normalize_product_key,
    prefer_display_name,
)
from app.services.sales_insights_service import (
    SalesInsightsService,
    _customer_identity_expr,
    _fy_of_annual_label,
    _quarter_of_record,
)
from app.utils.period_calendar import month_label
from app.utils.quantity import _COUNT_UNITS, round_mt


@dataclass
class ResolvedFilters:
    """Filters after server-side resolution against the caller's authorized data."""

    distributor_id: Optional[int] = None
    distributor_name: Optional[str] = None
    segment: Optional[str] = None
    product: Optional[str] = None
    customer_key: Optional[str] = None
    customer_name: Optional[str] = None

    def display(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if self.segment:
            out["Segment"] = self.segment
        if self.distributor_name:
            out["Distributor"] = self.distributor_name
        if self.customer_name:
            out["Customer"] = self.customer_name
        if self.product:
            out["Product"] = self.product
        return out


@dataclass
class TotalResult:
    quantity_mt: float
    row_count: int
    customer_count: int
    product_count: int


@dataclass
class GroupRow:
    key: str
    label: str
    quantity_mt: float
    distributor: Optional[str] = None


@dataclass
class UnitAudit:
    count_unit_rows: int = 0
    unknown_unit_rows: int = 0


@dataclass
class MonthlyResult:
    months: List[Tuple[int, int, float]] = field(default_factory=list)
    non_monthly_mt: float = 0.0
    non_monthly_rows: int = 0


class ChatQueryService:
    """Read-only aggregations restricted to the caller's ``ChatScope``."""

    def __init__(
        self,
        db: Session,
        scope: ChatScope,
        *,
        statement_timeout_ms: Optional[int] = None,
    ) -> None:
        self.db = db
        self.scope = scope
        self.insights = SalesInsightsService(db)
        self._product_variants_cache: Dict[str, List[str]] = {}
        if statement_timeout_ms:
            self.db.execute(text(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"))

    # ---- base ---------------------------------------------------------------------

    def _base(self, query, rf: ResolvedFilters, *, apply_customer: bool = True):
        q = query.select_from(SalesRecord).join(
            Report, Report.id == SalesRecord.report_id
        ).join(Distributor, Distributor.id == SalesRecord.distributor_id)
        q = self.insights.scoped_query(
            q,
            distributor_id=rf.distributor_id,
            product=rf.product,
            segment=rf.segment,
            allowed_segments=self.scope.allowed_segments,
            allowed_companies=self.scope.allowed_companies,
        )
        if apply_customer and rf.customer_key:
            q = q.where(_customer_identity_expr() == rf.customer_key)
        return q

    def scoped(self, query, rf: Optional[ResolvedFilters] = None, period: Optional[PeriodRef] = None):
        """Public entry for other chat services (entity universe, suggestions)."""
        return apply_period(self._base(query, rf or ResolvedFilters()), period)

    # ---- totals -------------------------------------------------------------------

    def total(self, rf: ResolvedFilters, period: Optional[PeriodRef]) -> TotalResult:
        q = select(
            func.coalesce(func.sum(mt_quantity_expr()), 0),
            func.count(SalesRecord.id),
            func.count(distinct(_customer_identity_expr())),
        )
        qty, rows, customers = self.db.execute(self.scoped(q, rf, period)).one()
        products_q = self.scoped(select(distinct(SalesRecord.product)), rf, period)
        raw_products = [p for p in self.db.scalars(products_q).all() if p]
        return TotalResult(
            quantity_mt=round_mt(qty),
            row_count=int(rows or 0),
            customer_count=int(customers or 0),
            product_count=len(canonical_products(raw_products)),
        )

    # ---- grouping -----------------------------------------------------------------

    def grouped(
        self,
        rf: ResolvedFilters,
        period: Optional[PeriodRef],
        dimension: QueryDimension,
    ) -> List[GroupRow]:
        """All groups for a dimension, sorted by MT desc (callers slice top-N)."""
        mt = func.coalesce(func.sum(mt_quantity_expr()), 0)
        rows: List[GroupRow]
        if dimension == QueryDimension.CUSTOMER:
            ident = _customer_identity_expr()
            cols = [ident.label("ident"), func.min(SalesRecord.customer_name).label("name")]
            group = [ident]
            with_dist = rf.distributor_id is None
            if with_dist:
                cols.append(company_expr().label("company"))
                group.append(company_expr())
            q = self.scoped(select(*cols, mt.label("mt")), rf, period).where(ident.is_not(None))
            rows = []
            for r in self.db.execute(q.group_by(*group)).all():
                company = r.company if with_dist else None
                rows.append(
                    GroupRow(
                        key=f"{company or ''}|{r.ident}",
                        label=" ".join(str(r.name or "").split()),
                        quantity_mt=round_mt(r.mt),
                        distributor=company,
                    )
                )
        elif dimension == QueryDimension.PRODUCT:
            q = self.scoped(select(SalesRecord.product, mt.label("mt")), rf, period)
            buckets: Dict[str, GroupRow] = {}
            for name, qty in self.db.execute(q.group_by(SalesRecord.product)).all():
                raw = " ".join(str(name or "").split())
                key = normalize_product_key(raw)
                if not key:
                    continue
                if key in buckets:
                    bucket = buckets[key]
                    bucket.quantity_mt = round_mt(bucket.quantity_mt + float(qty or 0))
                    bucket.label = prefer_display_name(bucket.label, raw)
                else:
                    buckets[key] = GroupRow(key=key, label=raw, quantity_mt=round_mt(qty))
            rows = list(buckets.values())
        elif dimension == QueryDimension.DISTRIBUTOR:
            label = company_expr()
            q = self.scoped(
                select(SalesRecord.distributor_id, func.min(label).label("name"), mt.label("mt")),
                rf,
                period,
            ).group_by(SalesRecord.distributor_id)
            rows = [
                GroupRow(key=str(r.distributor_id), label=str(r.name or ""), quantity_mt=round_mt(r.mt))
                for r in self.db.execute(q).all()
            ]
        elif dimension == QueryDimension.SEGMENT:
            seg_key = func.lower(func.trim(func.coalesce(SalesRecord.segment, "")))
            q = self.scoped(
                select(seg_key.label("k"), func.min(SalesRecord.segment).label("name"), mt.label("mt")),
                rf,
                period,
            ).group_by(seg_key)
            rows = [
                GroupRow(key=r.k, label=str(r.name or "").strip() or "Unspecified", quantity_mt=round_mt(r.mt))
                for r in self.db.execute(q).all()
            ]
        elif dimension == QueryDimension.QUARTER:
            q = self.scoped(
                select(Report.reporting_month, SalesRecord.source_month, mt.label("mt")),
                rf,
                period,
            ).group_by(Report.reporting_month, SalesRecord.source_month)
            buckets = {}
            for reporting, source, qty in self.db.execute(q).all():
                fq = _quarter_of_record(reporting, source)
                if fq:
                    key, name = f"{fq[0]}-Q{fq[1]}", quarter_label(fq[0], fq[1])
                else:
                    key, name = "unassigned", "No quarter in source (annual rows)"
                bucket = buckets.setdefault(key, GroupRow(key=key, label=name, quantity_mt=0.0))
                bucket.quantity_mt = round_mt(bucket.quantity_mt + float(qty or 0))
            return sorted(buckets.values(), key=lambda g: g.key)
        elif dimension == QueryDimension.MONTH:
            monthly = self.monthly(rf, period)
            return [
                GroupRow(
                    key=f"{y:04d}-{m:02d}",
                    label=_month_name(y, m),
                    quantity_mt=qty,
                )
                for y, m, qty in monthly.months
            ]
        else:
            rows = []
        rows.sort(key=lambda g: (-g.quantity_mt, g.label.casefold()))
        return rows

    def monthly(self, rf: ResolvedFilters, period: Optional[PeriodRef]) -> MonthlyResult:
        """Only rows that carry a real calendar month. Quarter/annual rows are reported apart."""
        mt = func.coalesce(func.sum(mt_quantity_expr()), 0)
        q = self.scoped(
            select(
                SalesRecord.source_month,
                Report.reporting_month,
                SalesRecord.period,
                mt.label("mt"),
                func.count(SalesRecord.id).label("n"),
            ),
            rf,
            period,
        ).group_by(SalesRecord.source_month, Report.reporting_month, SalesRecord.period)
        months: Dict[Tuple[int, int], float] = {}
        result = MonthlyResult()
        for source, reporting, row_period, qty, n in self.db.execute(q).all():
            ym = actual_month_of_row(source, reporting, row_period)
            if ym is None:
                result.non_monthly_mt = round_mt(result.non_monthly_mt + float(qty or 0))
                result.non_monthly_rows += int(n or 0)
                continue
            months[ym] = round_mt(months.get(ym, 0.0) + float(qty or 0))
        result.months = [(y, m, months[(y, m)]) for (y, m) in sorted(months)]
        return result

    # ---- caveat inputs ------------------------------------------------------------

    def unit_audit(self, rf: ResolvedFilters, period: Optional[PeriodRef]) -> UnitAudit:
        unit_norm = func.upper(func.trim(func.coalesce(SalesRecord.unit, "")))
        origin_norm = func.upper(func.trim(func.coalesce(SalesRecord.original_unit, "")))
        count_unit = or_(unit_norm.in_(_COUNT_UNITS), origin_norm.in_(_COUNT_UNITS))
        known = or_(
            unit_norm.in_(_MT_UNIT_LABELS),
            unit_norm.in_(_KG_UNIT_LABELS),
            origin_norm.in_(_KG_UNIT_LABELS),
        )
        q = select(
            func.count(SalesRecord.id).filter(count_unit),
            func.count(SalesRecord.id).filter(and_(not_(count_unit), not_(known))),
        )
        counted, unknown = self.db.execute(self.scoped(q, rf, period)).one()
        return UnitAudit(count_unit_rows=int(counted or 0), unknown_unit_rows=int(unknown or 0))

    def rows_without_distributor(self, rf: ResolvedFilters, period: Optional[PeriodRef]) -> int:
        """Active rows with no linked distributor that would otherwise match (segment scope only)."""
        if rf.distributor_id is not None or self.scope.allowed_companies is not None:
            return 0
        q = (
            select(func.count(SalesRecord.id))
            .select_from(SalesRecord)
            .join(Report, Report.id == SalesRecord.report_id)
            .where(
                SalesRecord.distributor_id.is_(None),
                SalesRecord.is_deleted.is_(False),
                Report.is_deleted.is_(False),
            )
        )
        if self.scope.allowed_segments is not None:
            lowered = [s.casefold() for s in self.scope.allowed_segments if (s or "").strip()]
            if not lowered:
                return 0
            q = q.where(func.lower(SalesRecord.segment).in_(lowered))
        if rf.segment:
            q = q.where(func.lower(SalesRecord.segment) == rf.segment.strip().lower())
        if rf.product:
            variants = self.product_variants(rf.product)
            q = q.where(func.lower(SalesRecord.product).in_([v.lower() for v in variants]))
        if rf.customer_key:
            q = q.where(_customer_identity_expr() == rf.customer_key)
        q = apply_period(q, period)
        return int(self.db.execute(q).scalar() or 0)

    def product_variants(self, product: str) -> List[str]:
        if product not in self._product_variants_cache:
            self._product_variants_cache[product] = expand_product_filter(
                product, self.insights.sales.distinct_products()
            )
        return self._product_variants_cache[product]

    # ---- scope metadata -----------------------------------------------------------

    def available_fiscal_years(self, rf: Optional[ResolvedFilters] = None) -> List[int]:
        q = self.scoped(
            select(Report.reporting_month, SalesRecord.source_month, SalesRecord.period).distinct(),
            rf,
        )
        years = set()
        for reporting, source, row_period in self.db.execute(q).all():
            fq = _quarter_of_record(reporting, source)
            if fq:
                years.add(fq[0])
                continue
            annual = _fy_of_annual_label(reporting)
            if annual:
                years.add(annual)
                continue
            ym = actual_month_of_row(source, reporting, row_period)
            if ym:
                years.add(ym[0] if ym[1] >= 4 else ym[0] - 1)
        return sorted(years, reverse=True)


def _month_name(year: int, month: int) -> str:
    return month_label(month, year)
