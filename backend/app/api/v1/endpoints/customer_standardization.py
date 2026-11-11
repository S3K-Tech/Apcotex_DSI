"""Customer Standardization Workbench endpoints."""

from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.erp import _ensure_email_access
from app.database.session import get_db
from app.dependencies.rbac import RequireUser, segment_scope_for_user
from app.exceptions import ValidationAppError
from app.schemas.common import DataResponse
from app.schemas.customer_standardization import CustomerApprovalRequest
from app.services.customer_standardization.learning_service import CustomerLearningService
from app.services.customer_standardization.matching import CustomerMatcher
from app.services.customer_standardization.workbench_service import CustomerWorkbenchService, raw_customer

router = APIRouter(prefix="/customer-standardization", tags=["Customer Standardization"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/emails/{email_id}/workbench", response_model=DataResponse[Dict[str, Any]])
def email_customer_workbench(
    email_id: int,
    current: RequireUser,
    db: DbSession,
    attachment_id: Optional[int] = Query(None, ge=1),
    refresh: bool = Query(False),
) -> DataResponse[Dict[str, Any]]:
    service = CustomerWorkbenchService(db)
    _ensure_email_access(current, db, service.get_email(email_id))
    return DataResponse(
        data=service.workbench(
            email_id, attachment_id, refresh=refresh, segments=segment_scope_for_user(current, db)
        )
    )


@router.get("/customers", response_model=DataResponse[Dict[str, Any]])
def search_customers(
    current: RequireUser,
    db: DbSession,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> DataResponse[Dict[str, Any]]:
    """Autocomplete over customer master, approved standard names and sales customers in scope."""
    matcher = CustomerMatcher.load(db, segments=segment_scope_for_user(current, db))
    return DataResponse(data={"items": matcher.search(q, limit)})


@router.post("/approve", response_model=DataResponse[Dict[str, Any]])
def approve_customers(
    payload: CustomerApprovalRequest,
    current: RequireUser,
    db: DbSession,
) -> DataResponse[Dict[str, Any]]:
    workbench = CustomerWorkbenchService(db)
    email = workbench.get_email(payload.email_id)
    _ensure_email_access(current, db, email)
    snap = workbench.snapshot(email, payload.attachment_id)
    known = {raw_customer(r).casefold() for r in (snap.rows or [])}
    known.discard("")
    unknown = [i.raw_customer for i in payload.items if raw_customer({"customer_name": i.raw_customer}).casefold() not in known]
    if unknown:
        raise ValidationAppError(
            "Some customers are not part of this attachment's extracted data.",
            details={"customers": unknown[:20]},
        )
    distributor = workbench.resolve_distributor(email)
    result = CustomerLearningService(db).approve(
        [i.model_dump() for i in payload.items],
        current,
        distributor_id=distributor["id"] if distributor else None,
        email_id=email.id,
        attachment_id=snap.attachment_id,
    )
    conflicts = len(result["conflicts"])
    message = f"{result['approved']} customer mapping(s) approved."
    if conflicts:
        message += f" {conflicts} already had a different approved mapping and were not changed."
    return DataResponse(data=result, message=message)
