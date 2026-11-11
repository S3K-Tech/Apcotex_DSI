"""Customer Standardization Workbench for one email attachment."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.email_message import EmailMessage
from app.services.customer_standardization.matching import (
    AUTO_MATCHED,
    METHOD_DISTRIBUTOR_MAPPING,
    METHOD_EXACT_AMBIGUOUS,
    METHOD_EXISTING_EXACT,
    METHOD_GLOBAL_MAPPING,
    METHOD_MAPPING_CONFLICT,
    METHOD_MASTER_EXACT,
    METHOD_SIMILARITY_AMBIGUOUS,
    METHOD_SIMILARITY_HIGH,
    METHOD_SIMILARITY_MEDIUM,
    NEEDS_REVIEW,
    CustomerMatch,
    CustomerMatcher,
)
from app.services.customer_standardization.normalize import clean_display
from app.services.product_master.workbench_service import ProductWorkbenchService, _quantity

_METHOD_LABEL = {
    METHOD_DISTRIBUTOR_MAPPING: "Approved (distributor)",
    METHOD_GLOBAL_MAPPING: "Approved (global)",
    METHOD_MASTER_EXACT: "Exact customer master match",
    METHOD_EXISTING_EXACT: "Exact existing customer",
    METHOD_EXACT_AMBIGUOUS: "Several existing spellings",
    METHOD_SIMILARITY_HIGH: "Similar name (high)",
    METHOD_SIMILARITY_MEDIUM: "Similar name (medium)",
    METHOD_SIMILARITY_AMBIGUOUS: "Several similar customers",
    METHOD_MAPPING_CONFLICT: "Conflicting approved mappings",
}

STATUS_APPROVED = "APPROVED"


def raw_customer(row: Dict[str, Any]) -> str:
    return clean_display(row.get("customer_name") or row.get("customer"))


def _bucket(result: CustomerMatch) -> str:
    if result.approved:
        return "approved"
    if result.status == AUTO_MATCHED:
        return "auto_matched"
    if result.status == NEEDS_REVIEW:
        return "needs_review"
    return "unmapped"


_UI_STATUS = {
    "approved": STATUS_APPROVED,
    "auto_matched": AUTO_MATCHED,
    "needs_review": NEEDS_REVIEW,
    "unmapped": "UNMAPPED",
}


class CustomerWorkbenchService:
    """Groups the attachment's customers and matches each distinct raw name once."""

    def __init__(self, db: Session) -> None:
        self.db = db
        # Snapshot / attachment / distributor resolution are shared read-only
        # with the product workbench so both work on the same extracted rows.
        self.source = ProductWorkbenchService(db)

    def get_email(self, email_id: int) -> EmailMessage:
        return self.source.get_email(email_id)

    def resolve_distributor(self, email: EmailMessage) -> Optional[Dict[str, Any]]:
        return self.source.resolve_distributor(email)

    def snapshot(self, email: EmailMessage, attachment_id: Optional[int], *, refresh: bool = False):
        return self.source.snapshot(email, attachment_id, refresh=refresh)

    def workbench(
        self,
        email_id: int,
        attachment_id: Optional[int],
        *,
        refresh: bool = False,
        segments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        email = self.get_email(email_id)
        snap = self.snapshot(email, attachment_id, refresh=refresh)
        matcher = CustomerMatcher.load(self.db, segments=segments)
        distributor = self.resolve_distributor(email)
        distributor_id = distributor["id"] if distributor else None

        grouped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        blank_rows = 0
        for row in snap.rows or []:
            raw = raw_customer(row)
            if not raw:
                blank_rows += 1
                continue
            entry = grouped.setdefault(raw, {"row_count": 0, "quantity": 0.0})
            entry["row_count"] += 1
            entry["quantity"] += _quantity(row)

        metrics = {"total_distinct": 0, "approved": 0, "auto_matched": 0, "needs_review": 0, "unmapped": 0}
        items: List[Dict[str, Any]] = []
        for raw, agg in grouped.items():
            result = matcher.match(raw, distributor_id)
            bucket = _bucket(result)
            metrics["total_distinct"] += 1
            metrics[bucket] += 1
            item = result.to_dict()
            item.update(
                {
                    "distributor": distributor["name"] if distributor else None,
                    "row_count": agg["row_count"],
                    "quantity": round(agg["quantity"], 3),
                    "bucket": bucket,
                    "ui_status": _UI_STATUS[bucket],
                    "method_label": _METHOD_LABEL.get(result.method, ""),
                }
            )
            items.append(item)
        order = {"needs_review": 0, "unmapped": 1, "auto_matched": 2, "approved": 3}
        items.sort(key=lambda i: (order.get(i["bucket"], 9), i["raw_customer"].casefold()))

        extracted = snap.updated_at or snap.created_at
        return {
            "email_id": email.id,
            "email_subject": email.subject,
            "attachment_id": snap.attachment_id,
            "attachment_name": snap.attachment_name,
            "attachments": [{"id": a.id, "name": a.file_name} for a in self.source._attachments(email)],
            "distributor": distributor,
            "reference_count": matcher.reference_count,
            "extracted_at": extracted.isoformat() if extracted else None,
            "row_count": snap.row_count,
            "blank_rows": blank_rows,
            "metrics": metrics,
            "items": items,
        }
