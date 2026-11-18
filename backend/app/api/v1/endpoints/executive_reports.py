"""Quarterly executive report endpoints (Visualizations & Analytics)."""

from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.dependencies.rbac import (
    RequireAdmin,
    RequireUser,
    distributor_companies_scope_for_user,
    segment_scope_for_user,
)
from app.schemas.common import DataResponse
from app.schemas.executive_report import GenerateReportRequest, SaveRecipientsRequest, SendReportEmailRequest
from app.services.executive_report.service import ExecutiveReportService, pdf_filename, report_payload

router = APIRouter(prefix="/executive-reports", tags=["Executive Reports"])

DbSession = Annotated[Session, Depends(get_db)]


def get_report_service(db: DbSession) -> ExecutiveReportService:
    return ExecutiveReportService(db)


ReportService = Annotated[ExecutiveReportService, Depends(get_report_service)]


@router.post("/generate", response_model=DataResponse[Dict[str, Any]])
def generate_report(
    payload: GenerateReportRequest,
    current: RequireUser,
    db: DbSession,
    service: ReportService,
) -> DataResponse[Dict[str, Any]]:
    """Analytics snapshot for the selected filters plus the LLM executive summary."""
    report = service.generate(
        payload.filters.model_dump(),
        current,
        allowed_segments=segment_scope_for_user(current, db),
        allowed_companies=distributor_companies_scope_for_user(current, db),
    )
    return DataResponse(data=report_payload(report, current))


@router.get("/recipients", response_model=DataResponse[Dict[str, Any]])
def list_recipients(current: RequireAdmin, service: ReportService) -> DataResponse[Dict[str, Any]]:
    return DataResponse(data={"recipients": service.recipients()})


@router.put("/recipients", response_model=DataResponse[Dict[str, Any]])
def save_recipients(
    payload: SaveRecipientsRequest, current: RequireAdmin, service: ReportService
) -> DataResponse[Dict[str, Any]]:
    items = [r.model_dump() for r in payload.recipients]
    return DataResponse(data={"recipients": service.save_recipients(items, current)})


@router.get("/{report_id}/pdf")
def download_pdf(
    report_id: int,
    current: RequireUser,
    service: ReportService,
    distributor_id: Optional[int] = Query(None),
    distributor: Optional[str] = Query(None),
    customer: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    fiscal_year_start: Optional[int] = Query(None),
    period: Optional[str] = Query("full_year"),
    start_month: Optional[str] = Query(None),
    end_month: Optional[str] = Query(None),
) -> Response:
    """PDF of a stored report. The current filter selection must match the report's."""
    report = service.get_for_user(report_id, current)
    service.ensure_current(
        report,
        dict(
            distributor_id=distributor_id, distributor=distributor, customer=customer, product=product,
            location=location, segment=segment, fiscal_year_start=fiscal_year_start, period=period,
            start_month=start_month, end_month=end_month,
        ),
    )
    pdf = service.render_pdf(report)
    service.record_download(report, current)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename(report)}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{report_id}/email", response_model=DataResponse[Dict[str, Any]])
def email_report(
    report_id: int,
    payload: SendReportEmailRequest,
    current: RequireAdmin,
    service: ReportService,
) -> DataResponse[Dict[str, Any]]:
    """Send the report PDF to the selected recipients. Explicit, manual action only."""
    report = service.get_for_user(report_id, current)
    service.ensure_current(report, payload.filters.model_dump())
    log = service.send_email(
        report,
        recipients=[r.model_dump() for r in payload.recipients],
        idempotency_key=payload.idempotency_key,
        user=current,
    )
    return DataResponse(
        data={
            "status": log.status,
            "recipients": log.recipients,
            "subject": log.subject,
            "attachment_name": log.attachment_name,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        }
    )
