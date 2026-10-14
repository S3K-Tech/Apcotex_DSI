"""Read-only diagnostic: active reports filed under an annual period (``FY 2026-27``).

For each one it shows the source email subject, the quarters that subject allows, the
quarter each row belongs to by its own source month, the active quarter report the row
would move into, and whether the row already exists there (exact / quantity differs).
Nothing is written: the session runs in a READ ONLY transaction and is rolled back.

Usage:
    python -m scripts.diagnose_annual_period_reports
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select, text

from app.database.session import SessionLocal
from app.erp_parser.documents.row_quarters import allowed_quarter_list, subject_fy_start
from app.models.email_message import EmailMessage
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.utils.email_subject_parser import parse_email_subject
from app.utils.period_calendar import (
    fy_quarter_label,
    fy_start_for_calendar_month,
    parse_month_label,
    parse_quarter_label,
    quarter_of_month,
)


def _identity(row: SalesRecord) -> tuple:
    return tuple(
        str(v or "").strip().lower()
        for v in (row.customer_name, row.product, row.source_month, row.segment, row.location)
    )


def _row_quarter(row: SalesRecord):
    hit = parse_month_label(str(row.source_month or ""))
    if not hit:
        return None
    month, year = hit
    return fy_quarter_label(fy_start_for_calendar_month(month, year), quarter_of_month(month))


def main() -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        reports = [
            r
            for r in db.scalars(select(Report).where(Report.is_deleted.is_(False))).all()
            if (spec := parse_quarter_label(str(r.reporting_month or ""))) is None or spec.kind != "quarter"
        ]
        print(f"Active reports with a non-quarter period: {len(reports)}")
        for report in reports:
            email = db.get(EmailMessage, report.email_message_id) if report.email_message_id else None
            meta = parse_email_subject(email.subject) if email else {}
            allowed = allowed_quarter_list(meta) if email else []
            fy_start = subject_fy_start(meta) if email else None
            if len(allowed) > 1:
                kind = "MULTI-QUARTER SUBJECT (misfiled; candidate for correction)"
            elif email and not allowed:
                kind = "FULL-FY SUBJECT (existing annual behaviour; business decision)"
            else:
                kind = "OTHER (inspect manually)"
            rows = db.scalars(
                select(SalesRecord).where(
                    SalesRecord.report_id == report.id, SalesRecord.is_deleted.is_(False)
                )
            ).all()
            print(
                f"\nReport {report.id} | period={report.reporting_month} | distributor_id={report.distributor_id}"
                f" | rows={len(rows)} | email_id={report.email_message_id}"
                f"\n  subject: {email.subject if email else '-'}"
                f"\n  class: {kind} | allowed={allowed or '-'} | fy_start={fy_start}"
            )

            plan: dict = defaultdict(Counter)
            for row in rows:
                quarter = _row_quarter(row)
                if quarter is None:
                    plan["REVIEW: no source month"]["rows"] += 1
                    plan["REVIEW: no source month"]["qty"] += float(row.quantity or 0)
                    continue
                spec = parse_quarter_label(quarter)
                if fy_start is not None and spec.year != fy_start:
                    key = f"REVIEW: {quarter} outside subject FY"
                elif allowed and f"Q{spec.quarter}" not in allowed:
                    key = f"REVIEW: {quarter} outside subject quarters"
                else:
                    key = quarter
                plan[key]["rows"] += 1
                plan[key]["qty"] += float(row.quantity or 0)

            for key in sorted(plan):
                stats = plan[key]
                line = f"  -> {key}: rows={stats['rows']} qty={stats['qty']:.3f}"
                if not key.startswith("REVIEW"):
                    target = db.scalar(
                        select(Report).where(
                            Report.is_deleted.is_(False),
                            Report.distributor_id == report.distributor_id,
                            Report.reporting_month == key,
                        )
                    )
                    if target is None:
                        line += " | target: none (a quarter report would be created)"
                    else:
                        existing = {
                            _identity(r): r.quantity
                            for r in db.scalars(
                                select(SalesRecord).where(
                                    SalesRecord.report_id == target.id,
                                    SalesRecord.is_deleted.is_(False),
                                )
                            ).all()
                        }
                        exact = changed = 0
                        for row in rows:
                            if _row_quarter(row) != key or _identity(row) not in existing:
                                continue
                            if existing[_identity(row)] == row.quantity:
                                exact += 1
                            else:
                                changed += 1
                        line += f" | target: report {target.id} | already there={exact} | qty differs={changed}"
                print(line)
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
