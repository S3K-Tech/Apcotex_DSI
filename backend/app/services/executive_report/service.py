"""Quarterly executive report: generate, retrieve, render PDF, configure recipients, email."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.dependencies.rbac import RequestUser
from app.enums import AuditAction, AuditModule, AuditStatus, UserRole
from app.exceptions import AppException, ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.models.executive_report import ExecutiveReport, ExecutiveReportEmail, ManagementRecipient
from app.schemas.audit import AuditTrailCreate
from app.services.audit_service import AuditService
from app.services.executive_report.analytics import build_snapshot
from app.services.executive_report.filters import (
    filter_key,
    normalize_filters,
    period_label,
    report_filename_stem,
    report_title,
)
from app.services.executive_report.pdf import build_pdf
from app.services.executive_report.summary import generate_summary
from app.services.llm_settings_service import LlmSettingsService

logger = get_logger(__name__)

MANAGEMENT_ROLES = (
    ("MD", "Managing Director"),
    ("CFO", "Chief Financial Officer"),
    ("CMO", "Chief Marketing Officer"),
    ("CIO", "Chief Information Officer"),
)
_ROLE_LABELS = dict(MANAGEMENT_ROLES)
MAX_RECIPIENTS = 20
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024
DUPLICATE_WINDOW = timedelta(minutes=2)
STATUS_SENDING, STATUS_SENT, STATUS_FAILED = "SENDING", "SENT", "FAILED"

EMAIL_BODY = """Dear Management Team,

Please find attached the {title} for the selected reporting period ({period}).

This is a Secondary Sales report based on distributor sales to customers. The attachment includes the sales performance dashboard, product-wise and customer-wise analysis, distributor performance, key business observations, and management recommendations.

Regards,
APCOTEX Sales Insights"""


def _default_completer(**kwargs):
    from app.llm.bedrock_client import complete

    return complete(**kwargs)


def is_admin(user: RequestUser) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


def email_subject(report: ExecutiveReport) -> str:
    title = (report.analytics or {}).get("report_title") or report_title(report.filters or {})
    return f"APCOTEX | {title} | {report.period_label}"


def pdf_filename(report: ExecutiveReport) -> str:
    filters = report.filters or {}
    return f"{report_filename_stem(filters)}.pdf"


def clean_email(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationAppError("Email address is required")
    try:
        return validate_email(text, check_deliverability=False).normalized
    except EmailNotValidError:
        raise ValidationAppError(f"'{text}' is not a valid email address") from None


class ExecutiveReportService:
    def __init__(self, db: Session, *, completer: Optional[Callable[..., Any]] = None) -> None:
        self.db = db
        self.completer = completer or _default_completer
        self.audit = AuditService(db)

    # ------------------------------------------------------------ generate
    def generate(
        self,
        raw_filters: Dict[str, Any],
        user: RequestUser,
        *,
        allowed_segments: Optional[List[str]],
        allowed_companies: Optional[List[str]],
    ) -> ExecutiveReport:
        filters = normalize_filters(raw_filters)
        if filters["period"] == "custom" and not (filters["start_month"] and filters["end_month"]):
            raise ValidationAppError("Select the From and To quarter for a custom range.")
        llm = LlmSettingsService(self.db)
        if not llm.is_enabled():
            raise ValidationAppError("LLM features are disabled in Settings. An administrator must enable them.")

        snapshot = build_snapshot(
            self.db, filters, allowed_segments=allowed_segments, allowed_companies=allowed_companies
        )
        if int(snapshot["kpis"].get("record_count") or 0) == 0:
            raise ValidationAppError(
                "No sales records exist for the selected filters, so there is nothing to summarise."
            )

        model_id = llm.get_model()
        summary, completion = generate_summary(snapshot, completer=self.completer, model_id=model_id)
        report = ExecutiveReport(
            filter_key=filter_key(filters),
            filters=filters,
            period_label=period_label(filters),
            access_scope={"segments": allowed_segments, "companies": allowed_companies},
            analytics=snapshot,
            summary=summary,
            provider=llm.get_provider(),
            model=getattr(completion, "model_id", None) or model_id,
            created_by=user.user_id,
            created_by_name=user.name,
        )
        self.db.add(report)
        self.db.flush()
        usage = completion.usage() if hasattr(completion, "usage") else None
        if usage:
            llm.record_usage(purpose="executive_summary", usage=usage, model=report.model, actor=user.name)
        self._audit(
            user,
            AuditAction.GENERATED,
            f"Generated quarterly executive summary for {report.period_label}",
            report,
            {"filters": filters, "removed_statements": summary["verification"]["removed_statements"]},
        )
        return report

    # ------------------------------------------------------------ access
    def get_for_user(self, report_id: int, user: RequestUser) -> ExecutiveReport:
        report = self.db.get(ExecutiveReport, report_id)
        if report is None:
            raise NotFoundError("Report not found")
        if not is_admin(user) and (user.user_id is None or report.created_by != user.user_id):
            raise ForbiddenError("You do not have access to this report")
        return report

    @staticmethod
    def ensure_current(report: ExecutiveReport, raw_filters: Dict[str, Any]) -> None:
        """Refuse a report generated for a different filter selection."""
        if filter_key(raw_filters) != report.filter_key:
            raise ConflictError(
                "The filters have changed since this report was generated. "
                "Generate the executive summary again for the current selection.",
                details={"report_period": report.period_label},
            )

    def render_pdf(self, report: ExecutiveReport) -> bytes:
        return build_pdf(
            snapshot=report.analytics,
            summary=report.summary,
            generated_at=report.created_at or datetime.now(timezone.utc),
            generated_by=report.created_by_name,
            model=report.model,
        )

    def record_download(self, report: ExecutiveReport, user: RequestUser) -> None:
        self._audit(user, AuditAction.DOWNLOADED, f"Downloaded executive report PDF for {report.period_label}", report)

    # ------------------------------------------------------------ recipients
    def recipients(self) -> List[Dict[str, Any]]:
        stored = {r.role_key: r for r in self.db.scalars(select(ManagementRecipient)).all()}
        out = []
        for key, label in MANAGEMENT_ROLES:
            row = stored.get(key)
            out.append(
                {
                    "role": key,
                    "role_label": label,
                    "name": row.name if row else None,
                    "email": row.email if row else None,
                    "updated_by": row.updated_by_name if row else None,
                    "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
                }
            )
        return out

    def save_recipients(self, items: List[Dict[str, Any]], user: RequestUser) -> List[Dict[str, Any]]:
        if not is_admin(user):
            raise ForbiddenError("Only administrators can configure management recipients")
        stored = {r.role_key: r for r in self.db.scalars(select(ManagementRecipient)).all()}
        changes = []
        for item in items:
            role = str(item.get("role") or "").upper()
            if role not in _ROLE_LABELS:
                raise ValidationAppError(f"Unknown management role '{item.get('role')}'")
            email = clean_email(item["email"]) if str(item.get("email") or "").strip() else None
            name = " ".join(str(item.get("name") or "").split()) or None
            row = stored.get(role)
            if row is None:
                row = ManagementRecipient(role_key=role)
                self.db.add(row)
                stored[role] = row
            if (row.email, row.name) != (email, name):
                changes.append({"role": role, "previous": row.email, "new": email})
            row.email, row.name = email, name
            row.updated_by, row.updated_by_name = user.user_id, user.name
        self.db.flush()
        if changes:
            self._audit(user, AuditAction.UPDATED, "Updated management report recipients", None, {"changes": changes})
        return self.recipients()

    # ------------------------------------------------------------ email
    def _clean_recipients(self, items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        seen, out = set(), []
        for item in items:
            email = clean_email(item.get("email"))
            if email.casefold() in seen:
                continue
            seen.add(email.casefold())
            role = str(item.get("role") or "").upper()
            out.append(
                {
                    "role": role if role in _ROLE_LABELS else "CUSTOM",
                    "name": " ".join(str(item.get("name") or "").split()) or _ROLE_LABELS.get(role, ""),
                    "email": email,
                }
            )
        if not out:
            raise ValidationAppError("Select at least one recipient")
        if len(out) > MAX_RECIPIENTS:
            raise ValidationAppError(f"A report can be sent to at most {MAX_RECIPIENTS} recipients")
        return out

    def send_email(
        self,
        report: ExecutiveReport,
        *,
        recipients: List[Dict[str, Any]],
        idempotency_key: str,
        user: RequestUser,
        graph_client: Any = None,
    ) -> ExecutiveReportEmail:
        if not is_admin(user):
            raise ForbiddenError("Only administrators can email management reports")
        key = (idempotency_key or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", key):
            raise ValidationAppError("A valid send request id is required")
        to = self._clean_recipients(recipients)

        existing = self.db.scalar(select(ExecutiveReportEmail).where(ExecutiveReportEmail.idempotency_key == key))
        if existing is not None:
            if existing.status == STATUS_SENDING:
                raise ConflictError("This email is already being sent")
            return existing
        addresses = sorted(r["email"].casefold() for r in to)
        recent = self.db.scalars(
            select(ExecutiveReportEmail).where(
                ExecutiveReportEmail.report_id == report.id,
                ExecutiveReportEmail.status.in_((STATUS_SENT, STATUS_SENDING)),
                ExecutiveReportEmail.created_at >= datetime.now(timezone.utc) - DUPLICATE_WINDOW,
            )
        ).all()
        for row in recent:
            if sorted(r["email"].casefold() for r in row.recipients) == addresses:
                raise ConflictError("This report was just sent to the same recipients. Please wait before resending.")

        log = ExecutiveReportEmail(
            report_id=report.id,
            idempotency_key=key,
            recipients=to,
            subject=email_subject(report),
            status=STATUS_SENDING,
            sent_by=user.user_id,
            sent_by_name=user.name,
        )
        self.db.add(log)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictError("This email is already being sent") from None

        error: Optional[str] = None
        try:
            pdf = self.render_pdf(report)
            if not pdf:
                raise ValidationAppError("The report PDF could not be generated")
            if len(pdf) > MAX_ATTACHMENT_BYTES:
                raise ValidationAppError("The report PDF is too large to email")
            log.attachment_name, log.attachment_bytes = pdf_filename(report), len(pdf)
            if graph_client is None:
                from app.integrations.graph.client import GraphClient

                graph_client = GraphClient()
            graph_client.send_mail(
                subject=log.subject,
                body_text=EMAIL_BODY.format(
                    period=report.period_label,
                    title=(report.analytics or {}).get("report_title") or report_title(report.filters or {}),
                ),
                to=[{"name": r["name"], "email": r["email"]} for r in to],
                attachments=[{"name": log.attachment_name, "content": pdf, "content_type": "application/pdf"}],
            )
            log.status, log.sent_at = STATUS_SENT, datetime.now(timezone.utc)
        except AppException as exc:
            error = exc.message
        except Exception as exc:  # noqa: BLE001
            logger.exception("Executive report email failed | report_id={}", report.id)
            error = f"Unexpected error ({type(exc).__name__})"
        if error:
            log.status, log.error = STATUS_FAILED, error

        self._audit(
            user,
            "Report Emailed" if log.status == STATUS_SENT else AuditAction.FAILED,
            f"Executive report email {'sent' if log.status == STATUS_SENT else 'failed'} for {report.period_label}",
            report,
            {"recipients": to, "status": log.status, "error": error, "email_log_id": log.id},
            status=AuditStatus.SUCCESS.value if log.status == STATUS_SENT else AuditStatus.FAILED.value,
        )
        self.db.commit()
        if error:
            raise AppException(
                message=f"The email was not sent: {error}",
                status_code=502,
                error_code="EMAIL_SEND_FAILED",
            )
        return log

    # ------------------------------------------------------------ audit
    def _audit(
        self,
        user: RequestUser,
        action: AuditAction | str,
        details: str,
        report: Optional[ExecutiveReport],
        metadata: Optional[Dict[str, Any]] = None,
        *,
        status: str = AuditStatus.SUCCESS.value,
    ) -> None:
        self.audit.log(
            AuditTrailCreate(
                user_name=user.name or "unknown",
                user_role=user.role.value,
                user_id=user.user_id,
                action=action,
                details=details,
                module=AuditModule.VISUALIZATION.value,
                status=status,
                entity_type="executive_report",
                entity_id=str(report.id) if report else None,
                report_name=report.period_label if report else None,
                extra_metadata={**(metadata or {}), **({"period": report.period_label} if report else {})},
            )
        )


def report_payload(report: ExecutiveReport, user: RequestUser) -> Dict[str, Any]:
    """API view of a report. Chart data comes from the stored snapshot."""
    snapshot = report.analytics or {}
    return {
        "id": report.id,
        "filter_key": report.filter_key,
        "filters": report.filters,
        "period_label": report.period_label,
        "report_title": snapshot.get("report_title") or report_title(report.filters or {}),
        "report_kind": snapshot.get("report_kind") or "",
        "filter_lines": snapshot.get("filter_lines") or [],
        "generated_at": report.created_at.isoformat() if report.created_at else None,
        "generated_by": report.created_by_name,
        "model": report.model,
        "kpis": snapshot.get("kpis") or {},
        "comparisons": [
            {k: c.get(k) for k in ("key", "description", "period", "available", "total_mt_display", "change_mt", "change_pct")}
            for c in snapshot.get("comparisons") or []
        ],
        "summary": report.summary,
        "email_subject": email_subject(report),
        "can_email": is_admin(user),
    }
