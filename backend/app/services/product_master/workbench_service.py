"""Product Standardization Workbench + standardized full preview for one email attachment."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.exceptions import NotFoundError, ValidationAppError
from app.models.email_message import EmailMessage
from app.models.product_standardization_snapshot import ProductStandardizationSnapshot
from app.services.product_master.matching import (
    AUTO_MATCHED,
    METHOD_DISTRIBUTOR_MAPPING,
    METHOD_GLOBAL_MAPPING,
    METHOD_MASTER_EXACT,
    NEEDS_REVIEW,
    UNMAPPED,
    MatchResult,
    ProductMatcher,
)
from app.services.product_master.normalize import clean_display

logger = get_logger(__name__)

MAX_SNAPSHOT_ROWS = 20_000
MAX_PAGE_SIZE = 200
_HIDDEN_KEYS = {"row_hash", "human_review", "errors"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _quantity(row: Dict[str, Any]) -> float:
    for key in ("sales_quantity_mt", "sales_quantity", "quantity", "qty"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _bucket(result: MatchResult) -> str:
    if result.method in (METHOD_DISTRIBUTOR_MAPPING, METHOD_GLOBAL_MAPPING):
        return "approved"
    if result.method == METHOD_MASTER_EXACT:
        return "auto_standardized"
    if result.status == NEEDS_REVIEW:
        return "awaiting_review"
    return "unmapped"


_METHOD_LABEL = {
    METHOD_DISTRIBUTOR_MAPPING: "Approved (distributor)",
    METHOD_GLOBAL_MAPPING: "Approved (global)",
    METHOD_MASTER_EXACT: "Exact master match",
}


class ProductWorkbenchService:
    def __init__(self, db: Session) -> None:
        self.db = db
        from app.services.erp_ingest_service import ERPIngestService

        self.erp = ERPIngestService(db)

    # --------------------------------------------------------------- context
    def get_email(self, email_id: int) -> EmailMessage:
        email = self.db.get(EmailMessage, email_id)
        if email is None or getattr(email, "is_deleted", False):
            raise NotFoundError("Email not found")
        return email

    def _attachments(self, email: EmailMessage) -> List[Any]:
        return self.erp._list_excel_attachments(email)

    def _pick_attachment(self, email: EmailMessage, attachment_id: Optional[int]) -> Any:
        attachments = self._attachments(email)
        if not attachments:
            raise ValidationAppError("This email has no Excel, PDF, or Word attachment to standardize.")
        if attachment_id is None:
            return attachments[0]
        for att in attachments:
            if int(att.id) == int(attachment_id):
                return att
        raise NotFoundError("Attachment not found on this email")

    def resolve_distributor(self, email: EmailMessage) -> Optional[Dict[str, Any]]:
        match = self.erp.resolve_distributor_from_subject(email)
        if match:
            return {"id": int(match["id"]), "name": match.get("company") or match.get("name")}
        senders = self.erp.resolve_distributors_for_sender(email.sender_email)
        if len(senders) == 1:
            return {"id": int(senders[0]["id"]), "name": senders[0].get("company") or senders[0].get("name")}
        return None

    # -------------------------------------------------------------- snapshot
    def _parse_attachment(self, email: EmailMessage, att: Any) -> Tuple[List[Dict[str, Any]], str]:
        path = Path(att.file_path or "")
        if not path.is_file():
            raise ValidationAppError("Attachment file is missing on disk. Re-sync Outlook to download it again.")
        from app.erp_parser.documents.row_quarters import assign_row_periods, subject_fiscal_year_start
        from app.utils.email_subject_parser import parse_email_subject

        subject_meta = parse_email_subject(email.subject)
        subject_period = str(subject_meta.get("period") or "").strip()
        preferred_parser, known_fingerprint = self.erp._preferred_parser_pair(email)
        try:
            one = self.erp.parser.preview(
                path,
                fiscal_year_start=subject_fiscal_year_start(email.subject),
                reporting_quarter=subject_period or None,
                distributor_label=email.parsed_distributor or "",
                subject=email.subject,
                preferred_parser=preferred_parser,
                known_fingerprint=known_fingerprint,
                known_profiles=self.erp._known_profiles(email),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Workbench parse failed | email_id={} | attachment_id={} | err={}",
                email.id, att.id, type(exc).__name__,
            )
            raise ValidationAppError(
                "This attachment could not be extracted. Open the email Preview to review the parser result."
            ) from exc
        rows = [r for r in (one.get("rows") or []) if isinstance(r, dict)]
        if subject_period or subject_meta.get("allowed_quarters"):
            handled, _notes = assign_row_periods(rows, subject_meta)
            if not handled and subject_period:
                for row in rows:
                    row["period"] = subject_period
        return rows, str(one.get("parser_used") or "")

    def snapshot(
        self, email: EmailMessage, attachment_id: Optional[int], *, refresh: bool = False
    ) -> ProductStandardizationSnapshot:
        att = self._pick_attachment(email, attachment_id)
        snap = self.db.scalar(
            select(ProductStandardizationSnapshot).where(
                ProductStandardizationSnapshot.email_id == email.id,
                ProductStandardizationSnapshot.attachment_id == att.id,
            )
        )
        fresh = snap is not None and (snap.content_hash or "") == (att.content_hash or "")
        if fresh and not refresh:
            return snap

        rows, parser_used = self._parse_attachment(email, att)
        if len(rows) > MAX_SNAPSHOT_ROWS:
            rows = rows[:MAX_SNAPSHOT_ROWS]
        columns: "OrderedDict[str, None]" = OrderedDict()
        for row in rows:
            for key in row.keys():
                if key not in _HIDDEN_KEYS and not str(key).startswith("_"):
                    columns.setdefault(str(key), None)
        safe_rows = json.loads(json.dumps(_json_safe(rows)))
        distributor = self.resolve_distributor(email)
        if snap is None:
            snap = ProductStandardizationSnapshot(email_id=email.id, attachment_id=att.id)
            self.db.add(snap)
        snap.attachment_name = (att.file_name or f"attachment-{att.id}")[:500]
        snap.content_hash = att.content_hash
        snap.distributor_id = distributor["id"] if distributor else None
        snap.parser_used = parser_used[:100] or None
        snap.row_count = len(safe_rows)
        snap.columns = list(columns.keys())
        snap.rows = safe_rows
        self.db.flush()
        return snap

    # ------------------------------------------------------------- workbench
    def _context(self, email: EmailMessage, snap: ProductStandardizationSnapshot, matcher: ProductMatcher) -> Dict[str, Any]:
        distributor = self.resolve_distributor(email)
        return {
            "email_id": email.id,
            "email_subject": email.subject,
            "attachment_id": snap.attachment_id,
            "attachment_name": snap.attachment_name,
            "attachments": [
                {"id": a.id, "name": a.file_name} for a in self._attachments(email)
            ],
            "distributor": distributor,
            "master": {
                "configured": matcher.master_configured,
                "version_number": matcher.master_version.version_number if matcher.master_version else None,
                "product_count": len(matcher.master_descriptions),
            },
            "extracted_at": (snap.updated_at or snap.created_at).isoformat() if (snap.updated_at or snap.created_at) else None,
            "row_count": snap.row_count,
        }

    def workbench(self, email_id: int, attachment_id: Optional[int], *, refresh: bool = False) -> Dict[str, Any]:
        email = self.get_email(email_id)
        snap = self.snapshot(email, attachment_id, refresh=refresh)
        matcher = ProductMatcher.load(self.db)
        ctx = self._context(email, snap, matcher)
        distributor_id = ctx["distributor"]["id"] if ctx["distributor"] else None

        grouped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for row in snap.rows or []:
            raw = clean_display(row.get("product"))
            if not raw:
                continue
            entry = grouped.setdefault(raw, {"row_count": 0, "quantity": 0.0})
            entry["row_count"] += 1
            entry["quantity"] += _quantity(row)

        metrics = {"total_distinct": 0, "auto_standardized": 0, "awaiting_review": 0, "unmapped": 0, "approved": 0}
        items: List[Dict[str, Any]] = []
        for raw, agg in grouped.items():
            result = matcher.match(raw, distributor_id)
            bucket = _bucket(result)
            metrics["total_distinct"] += 1
            metrics[bucket] += 1
            item = result.to_dict()
            item.update(
                {
                    "distributor": ctx["distributor"]["name"] if ctx["distributor"] else None,
                    "row_count": agg["row_count"],
                    "quantity": round(agg["quantity"], 3),
                    "bucket": bucket,
                    "method_label": _METHOD_LABEL.get(result.method, ""),
                }
            )
            items.append(item)
        order = {"awaiting_review": 0, "unmapped": 1, "auto_standardized": 2, "approved": 3}
        items.sort(key=lambda i: (order.get(i["bucket"], 9), i["raw_product"].casefold()))
        return {**ctx, "metrics": metrics, "items": items}

    # ---------------------------------------------------------- full preview
    def full_preview(
        self,
        email_id: int,
        attachment_id: Optional[int],
        *,
        page: int = 1,
        page_size: int = 50,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        email = self.get_email(email_id)
        snap = self.snapshot(email, attachment_id)
        matcher = ProductMatcher.load(self.db)
        ctx = self._context(email, snap, matcher)
        distributor_id = ctx["distributor"]["id"] if ctx["distributor"] else None

        cache: Dict[str, MatchResult] = {}
        enriched: List[Dict[str, Any]] = []
        wanted = (status or "").strip().upper() or None
        needle = clean_display(search).casefold() if search else ""
        for row in snap.rows or []:
            raw = clean_display(row.get("product"))
            result = cache.get(raw)
            if result is None:
                result = matcher.match(raw, distributor_id) if raw else MatchResult(
                    raw="", key="", status=UNMAPPED, method="NONE"
                )
                cache[raw] = result
            if wanted and result.status != wanted:
                continue
            out = {k: row.get(k) for k in snap.columns}
            out["original_product"] = raw
            # Only approved or exact master values count as standardized; a
            # similarity suggestion is shown separately and never as approved.
            out["standardized_product"] = result.suggested if result.status == AUTO_MATCHED else None
            out["suggested_product"] = result.suggested if result.status == NEEDS_REVIEW else None
            out["standardization_status"] = result.status
            out["standardization_source"] = _METHOD_LABEL.get(result.method, "")
            if needle and not any(needle in str(v).casefold() for v in out.values() if v is not None):
                continue
            enriched.append(out)

        page_size = max(1, min(int(page_size or 50), MAX_PAGE_SIZE))
        total = len(enriched)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page or 1), pages))
        start = (page - 1) * page_size
        columns = [c for c in snap.columns if c != "product"]
        return {
            **ctx,
            "columns": ["original_product", "standardized_product", "standardization_status", *columns,
                        "suggested_product", "standardization_source"],
            "rows": enriched[start:start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        }
