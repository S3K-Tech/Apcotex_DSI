"""ERP email import against the disposable test database (synthetic data only).

The workbook parser is replaced by a stub that returns fixed rows, so these tests
exercise subject validation, period assignment, duplicate review, ADD THIS, report
grouping, and MT totals without reading real workbooks or calling an LLM.
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.enums import EmailProcessStatus
from app.exceptions import ValidationAppError
from app.models.email_message import EmailAttachment, EmailMessage
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.repositories.sales_record_repository import SalesRecordRepository
from app.services.consolidated_data_service import ConsolidatedDataService
from app.services.email_subject_service import EmailSubjectService
from app.services.erp_ingest.constants import DUPLICATE_REVIEW_MESSAGE
from app.services.erp_ingest_service import ERPIngestService
from app.utils.email_subject_parser import MISSING_PERIOD_MESSAGE
from app.utils.period_calendar import fy_annual_label, fy_quarter_label

Q1 = fy_quarter_label(2026, 1)
Q2 = fy_quarter_label(2026, 2)


class StubParser:
    def __init__(self, rows: List[Dict[str, Any]], overall: float) -> None:
        self.rows = rows
        self.overall = overall

    def preview(self, path, **_kwargs) -> Dict[str, Any]:
        return {
            "rows": copy.deepcopy(self.rows),
            "row_count": len(self.rows),
            "sheet_name": "Sheet1",
            "confidence": {"overall": self.overall, "breakdown": {}},
            "orchestrator_confidence": 0,
        }


def _service(db, rows: List[Dict[str, Any]], overall: float = 92.0) -> ERPIngestService:
    svc = ERPIngestService(db)
    svc.parser = StubParser(rows, overall)
    svc._available_columns = lambda *_a, **_k: []
    return svc


def _row(customer: str, qty: float, month: str = "", product: str = "TX 400") -> Dict[str, Any]:
    row: Dict[str, Any] = {"customer_name": customer, "product": product, "sales_quantity": qty}
    if month:
        row["source_month"] = month
    return row


def _email(db, tmp_path, subject: str, *, status: str = EmailProcessStatus.PARSED.value) -> EmailMessage:
    workbook = tmp_path / f"wb-{uuid4().hex}.xlsx"
    workbook.write_bytes(os.urandom(128))
    email = EmailMessage(
        graph_message_id=f"test-{uuid4()}",
        subject=subject,
        sender_email="distributor@apcotex.com",
        received_at=datetime.now(timezone.utc),
        has_attachments=True,
        process_status=EmailProcessStatus.UNREAD.value,
    )
    db.add(email)
    db.flush()
    EmailSubjectService(db).apply_to_email(email)
    email.process_status = status
    email.attachments.append(
        EmailAttachment(
            graph_attachment_id=f"att-{uuid4()}",
            file_name=workbook.name,
            file_path=str(workbook),
            is_excel=True,
        )
    )
    db.flush()
    return email


def _active_rows(db, distributor_id: int) -> List[SalesRecord]:
    return list(
        db.scalars(
            select(SalesRecord)
            .join(Report, Report.id == SalesRecord.report_id)
            .where(
                SalesRecord.distributor_id == distributor_id,
                SalesRecord.is_deleted.is_(False),
                Report.is_deleted.is_(False),
            )
            .order_by(SalesRecord.id)
        ).all()
    )


def _total_rows(db) -> int:
    return int(db.scalar(select(func.count(SalesRecord.id))) or 0)


# ---- Fix 2: missing period -------------------------------------------------------------


def test_missing_period_is_rejected_before_anything_is_persisted(db, tmp_path):
    email = _email(db, tmp_path, "NoPeriod Traders | South | Construction")
    assert email.subject_valid is False
    assert email.error_message == MISSING_PERIOD_MESSAGE

    # Simulate an email flagged valid before the rule existed, with a workbook quarter.
    email.subject_valid = True
    email.parsed_distributor = "NoPeriod Traders"
    email.detected_quarter = Q1
    db.flush()
    svc = _service(db, [_row("A", 10, "April 2026")])
    before = _total_rows(db)

    with pytest.raises(ValidationAppError) as imported:
        svc.import_approved(email_id=email.id, actor="tester", reporting_quarter=Q1)
    assert imported.value.message == MISSING_PERIOD_MESSAGE

    email.subject_valid = True
    with pytest.raises(ValidationAppError) as previewed:
        svc.preview_email(email.id, actor="tester")
    assert previewed.value.message == MISSING_PERIOD_MESSAGE
    assert _total_rows(db) == before


def test_add_cannot_bypass_a_missing_period(db, tmp_path):
    email = _email(db, tmp_path, "NoPeriod Two | South | Construction")
    email.subject_valid = True
    email.parsed_distributor = "NoPeriod Two"
    email.process_status = EmailProcessStatus.HUMAN_REVIEW.value
    db.flush()
    with pytest.raises(ValidationAppError) as exc:
        _service(db, [_row("A", 10, "April 2026")]).resolve_review(
            email.id, action="ADD", actor="tester"
        )
    assert exc.value.message == MISSING_PERIOD_MESSAGE


# ---- Q1 + Q2 --------------------------------------------------------------------------


def test_q1_q2_rows_land_in_their_own_quarter_reports(db, tmp_path):
    email = _email(db, tmp_path, "Halfyear Traders | West | Gloves | Q1+Q2 FY 2026-27 | MT")
    rows = [
        _row("Alpha", 10, "May 2026"),
        _row("Beta", 20, "August 2026"),
        _row("Gamma", 30, "October 2026"),  # Q3: outside the subject range
        _row("Delta", 40),  # no month: cannot be placed
    ]
    result = _service(db, rows).import_approved(email_id=email.id, actor="tester")

    assert result["records_inserted"] == 2
    assert result["quarters_imported"] == [Q1, Q2]
    assert result["reports_created"] == 2
    assert any("held for review" in s["reason"] for s in result["workbook_skips"])

    stored = _active_rows(db, result["distributor_id"])
    assert {(r.customer_name, r.period) for r in stored} == {("Alpha", Q1), ("Beta", Q2)}
    months = set(
        db.scalars(
            select(Report.reporting_month).where(Report.distributor_id == result["distributor_id"])
        ).all()
    )
    assert months == {Q1, Q2}
    assert fy_annual_label(2026) not in {r.period for r in stored}
    assert db.get(EmailMessage, email.id).process_status == EmailProcessStatus.INSERTED.value


# ---- Different segments coexist; a genuine repeat is caught ---------------------------


def test_segments_coexist_and_a_repeat_goes_to_review(db, tmp_path):
    rows = [_row("Alpha", 10, "April 2026"), _row("Beta", 5, "May 2026")]
    construction = _email(db, tmp_path, "Twoseg Traders | South | Construction | Q1 FY 2026-27 | MT")
    first = _service(db, rows).import_approved(email_id=construction.id, actor="tester")
    assert first["records_inserted"] == 2

    gloves = _email(db, tmp_path, "Twoseg Traders | South | Gloves | Q1 FY 2026-27 | MT")
    second = _service(db, rows).import_approved(email_id=gloves.id, actor="tester")
    assert not second.get("requires_review")
    assert second["records_inserted"] == 2

    dist_id = first["distributor_id"]
    stored = _active_rows(db, dist_id)
    assert sorted({r.segment for r in stored}) == ["Construction", "Gloves"]
    assert len({r.report_id for r in stored}) == 1  # one report per distributor + quarter

    page = ConsolidatedDataService(db).list_records(distributor="Twoseg Traders", limit=50)
    groups = [g for g in page.data if g.distributorId == dist_id]
    assert groups, "distributor missing from Consolidated Data"
    segments = {s.segment for g in groups for s in g.segments}
    assert segments == {"Construction", "Gloves"}
    assert {line.segment for g in groups for line in g.sales} == {"Construction", "Gloves"}
    options = ConsolidatedDataService(db).filter_options()
    assert {"Construction", "Gloves"} <= set(options.segments)

    repeat = _email(db, tmp_path, "Twoseg Traders | South | Construction | Q1 FY 2026-27 | MT")
    blocked = _service(db, rows).import_approved(email_id=repeat.id, actor="tester")
    assert blocked["requires_review"] is True
    assert blocked["records_inserted"] == 0
    assert blocked["duplicate_review"]["message"] == DUPLICATE_REVIEW_MESSAGE
    assert db.get(EmailMessage, repeat.id).process_status == EmailProcessStatus.INCREMENTAL_REVIEW.value
    assert len(_active_rows(db, dist_id)) == 4

    added = _service(db, rows).resolve_review(repeat.id, action="ADD", actor="tester")
    assert added["outcome"] == "duplicate_upload"
    assert added["records_inserted"] == 0
    assert added["status"] == EmailProcessStatus.DUPLICATE_UPLOAD.value
    assert len(_active_rows(db, dist_id)) == 4


# ---- Fix 4: subject-level warning and ADD THIS ------------------------------------------


def test_subject_duplicate_with_different_quantities_then_add(db, tmp_path):
    subject = "Recount Traders | North | Construction | Q1 FY 2026-27 | MT"
    original = [_row("Alpha", 10, "April 2026"), _row("Beta", 5, "April 2026")]
    first = _service(db, original).import_approved(
        email_id=_email(db, tmp_path, subject).id, actor="tester"
    )
    dist_id = first["distributor_id"]

    resubmitted = [
        _row("Alpha", 12, "April 2026"),  # modified quantity
        _row("Beta", 5, "April 2026"),  # identical
        _row("Gamma", 7, "April 2026"),  # new
    ]
    email = _email(db, tmp_path, subject)
    svc = _service(db, resubmitted)
    held = svc.import_approved(email_id=email.id, actor="tester")
    assert held["requires_review"] is True
    assert held["records_inserted"] == 0
    assert held["duplicate_review"]["periods"] == [Q1]
    assert len(_active_rows(db, dist_id)) == 2

    summary = svc.review_summary(email.id)
    assert summary["reviewable"] is True
    assert summary["same_segment_exists"] is True
    assert summary["low_confidence"] is False

    added = svc.resolve_review(email.id, action="ADD", actor="tester")
    assert added["outcome"] == "imported"
    assert added["records_inserted"] == 2
    assert added["status"] == EmailProcessStatus.INSERTED.value
    stored = [(r.customer_name, float(r.quantity)) for r in _active_rows(db, dist_id)]
    assert sorted(stored) == sorted(
        [("Alpha", 10.0), ("Beta", 5.0), ("Alpha", 12.0), ("Gamma", 7.0)]
    )


def test_subject_duplicate_is_flagged_even_when_every_row_is_new(db, tmp_path):
    subject = "Newrows Traders | North | Construction | Q1 FY 2026-27 | MT"
    _service(db, [_row("Alpha", 10, "April 2026")]).import_approved(
        email_id=_email(db, tmp_path, subject).id, actor="tester"
    )
    second = _service(db, [_row("Zeta", 3, "April 2026")]).import_approved(
        email_id=_email(db, tmp_path, subject).id, actor="tester"
    )
    assert second["requires_review"] is True
    assert second["records_inserted"] == 0


def test_monthly_subjects_compare_the_month(db, tmp_path):
    base = "Monthly Traders | East | Construction | {} | MT"
    april = _service(db, [_row("Alpha", 10)]).import_approved(
        email_id=_email(db, tmp_path, base.format("APRIL 2026")).id, actor="tester"
    )
    may = _service(db, [_row("Alpha", 11)]).import_approved(
        email_id=_email(db, tmp_path, base.format("MAY 2026")).id, actor="tester"
    )
    assert not may.get("requires_review")
    assert may["records_inserted"] == 1
    again = _service(db, [_row("Alpha", 99)]).import_approved(
        email_id=_email(db, tmp_path, base.format("APRIL 2026")).id, actor="tester"
    )
    assert again["requires_review"] is True
    months = sorted(r.source_month for r in _active_rows(db, april["distributor_id"]))
    assert months == ["April 2026", "May 2026"]


def test_low_confidence_needs_add_and_add_overrides_only_the_threshold(db, tmp_path):
    subject = "Lowconf Traders | West | Construction | Q1 FY 2026-27 | MT"
    rows = [_row("Alpha", 10, "April 2026")]

    email = _email(db, tmp_path, subject, status=EmailProcessStatus.HUMAN_REVIEW.value)
    svc = _service(db, rows, overall=60.0)
    with pytest.raises(ValidationAppError) as normal:
        svc.import_approved(email_id=email.id, actor="tester")
    assert normal.value.message == "No Excel attachments could be imported for this email."
    with pytest.raises(ValidationAppError):
        svc.import_approved(email_id=email.id, actor="tester", confirm_add=True)

    summary = svc.review_summary(email.id)
    assert summary["low_confidence"] is True
    assert summary["same_segment_exists"] is False

    added = svc.resolve_review(email.id, action="ADD", actor="tester")
    assert added["outcome"] == "imported"
    assert added["records_inserted"] == 1
    assert added["status"] == EmailProcessStatus.INSERTED.value


def test_add_still_fails_for_an_unparseable_workbook(db, tmp_path):
    email = _email(
        db,
        tmp_path,
        "Zeroconf Traders | West | Construction | Q1 FY 2026-27 | MT",
        status=EmailProcessStatus.HUMAN_REVIEW.value,
    )
    before = _total_rows(db)
    with pytest.raises(ValidationAppError):
        _service(db, [_row("Alpha", 10, "April 2026")], overall=0.0).resolve_review(
            email.id, action="ADD", actor="tester"
        )
    with pytest.raises(ValidationAppError):
        _service(db, [], overall=60.0).resolve_review(email.id, action="ADD", actor="tester")
    assert _total_rows(db) == before
    assert db.get(EmailMessage, email.id).process_status == EmailProcessStatus.HUMAN_REVIEW.value


def test_preview_reports_duplicate_and_low_confidence_separately(db, tmp_path):
    subject = "Preview Traders | South | Construction | Q1 FY 2026-27 | MT"
    _service(db, [_row("Alpha", 10, "April 2026")]).import_approved(
        email_id=_email(db, tmp_path, subject).id, actor="tester"
    )

    confident = _email(db, tmp_path, subject)
    preview = _service(db, [_row("Alpha", 11, "April 2026")]).preview_email(confident.id)
    assert preview["duplicate_review"]["detected"] is True
    assert preview["duplicate_review"]["periods"] == [Q1]
    assert confident.process_status == EmailProcessStatus.INCREMENTAL_REVIEW.value

    unsure = _email(db, tmp_path, subject)
    preview = _service(db, [_row("Zeta", 1, "April 2026")], overall=60.0).preview_email(unsure.id)
    assert preview["duplicate_review"]["detected"] is True
    assert unsure.process_status == EmailProcessStatus.HUMAN_REVIEW.value

    other_segment = _email(db, tmp_path, "Preview Traders | South | Gloves | Q1 FY 2026-27 | MT")
    preview = _service(db, [_row("Alpha", 10, "April 2026")]).preview_email(other_segment.id)
    assert preview["duplicate_review"] is None
    assert other_segment.process_status == EmailProcessStatus.PARSED.value


# ---- Fix 3: KG and MT totals -----------------------------------------------------------


def test_period_summaries_total_is_mt(db, tmp_path):
    kg = _service(db, [_row("Alpha", 5000, "April 2026")]).import_approved(
        email_id=_email(db, tmp_path, "Units Traders | South | Construction | Q1 FY 2026-27 | KG").id,
        actor="tester",
    )
    _service(db, [_row("Beta", 2, "April 2026")]).import_approved(
        email_id=_email(db, tmp_path, "Units Traders | South | Gloves | Q1 FY 2026-27 | MT").id,
        actor="tester",
    )
    units = sorted((r.unit, float(r.quantity)) for r in _active_rows(db, kg["distributor_id"]))
    assert units == [("KG", 5000.0), ("MT", 2.0)]

    summaries = SalesRecordRepository(db).period_summaries(distributor_id=kg["distributor_id"])
    q1 = next(s for s in summaries if s["label"] == Q1)
    assert q1["totalQuantity"] == pytest.approx(7.0)


def test_period_summaries_never_divides_mt_rows(db, tmp_path):
    rows = [_row("A", 1000, "April 2026"), _row("B", 500, "May 2026"), _row("C", 250, "June 2026")]
    mt = _service(db, rows).import_approved(
        email_id=_email(db, tmp_path, "Bigmt Traders | South | Paper | Q1 FY 2026-27 | MT").id,
        actor="tester",
    )
    summaries = SalesRecordRepository(db).period_summaries(distributor_id=mt["distributor_id"])
    q1 = next(s for s in summaries if s["label"] == Q1)
    assert q1["totalQuantity"] == pytest.approx(1750.0)
