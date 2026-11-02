"""FG Master data (Super Admin) and Product Standardization Workbench endpoints."""

from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.v1.endpoints.erp import _ensure_email_access
from app.database.session import get_db
from app.dependencies.rbac import RequireSuperAdmin, RequireUser
from app.exceptions import ValidationAppError
from app.schemas.common import DataResponse
from app.schemas.product_master import FGMasterPublishRequest, ProductApprovalRequest
from app.services.product_master.learning_service import ProductLearningService
from app.services.product_master.master_service import MAX_MASTER_FILE_BYTES, FGMasterService
from app.services.product_master.normalize import clean_display
from app.services.product_master.workbench_service import ProductWorkbenchService

router = APIRouter(prefix="/product-standardization", tags=["Product Standardization"])

DbSession = Annotated[Session, Depends(get_db)]


# ----------------------------------------------------------------- FG Master
@router.get("/master/status", response_model=DataResponse[Dict[str, Any]])
def master_status(_: RequireSuperAdmin, db: DbSession) -> DataResponse[Dict[str, Any]]:
    return DataResponse(data=FGMasterService(db).status())


@router.get("/master/versions", response_model=DataResponse[list])
def master_versions(_: RequireSuperAdmin, db: DbSession) -> DataResponse[list]:
    return DataResponse(data=FGMasterService(db).list_versions())


@router.post("/master/upload", response_model=DataResponse[Dict[str, Any]])
async def master_upload(
    current: RequireSuperAdmin,
    db: DbSession,
    file: UploadFile = File(...),
) -> DataResponse[Dict[str, Any]]:
    data = await file.read(MAX_MASTER_FILE_BYTES + 1)
    if len(data) > MAX_MASTER_FILE_BYTES:
        raise ValidationAppError(
            f"FG Master file exceeds the {MAX_MASTER_FILE_BYTES // (1024 * 1024)} MB limit."
        )
    result = FGMasterService(db).upload_draft(file.filename or "", data, current)
    return DataResponse(data=result, message="Draft validated. Review the changes, then publish.")


@router.get("/master/versions/{version_id}/diff", response_model=DataResponse[Dict[str, Any]])
def master_diff(version_id: int, _: RequireSuperAdmin, db: DbSession) -> DataResponse[Dict[str, Any]]:
    service = FGMasterService(db)
    return DataResponse(data=service.diff(service.get_version(version_id)))


@router.post("/master/versions/{version_id}/publish", response_model=DataResponse[Dict[str, Any]])
def master_publish(
    version_id: int,
    payload: FGMasterPublishRequest,
    current: RequireSuperAdmin,
    db: DbSession,
) -> DataResponse[Dict[str, Any]]:
    if not payload.confirm:
        raise ValidationAppError("Publishing requires explicit confirmation.")
    return DataResponse(
        data=FGMasterService(db).publish(version_id, current),
        message="FG Master published. It applies to new imports only.",
    )


@router.post("/master/versions/{version_id}/discard", response_model=DataResponse[Dict[str, Any]])
def master_discard(version_id: int, current: RequireSuperAdmin, db: DbSession) -> DataResponse[Dict[str, Any]]:
    return DataResponse(data=FGMasterService(db).discard(version_id, current))


@router.get("/master/products", response_model=DataResponse[Dict[str, Any]])
def master_products(
    _: RequireUser,
    db: DbSession,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> DataResponse[Dict[str, Any]]:
    """Autocomplete over the active FG Master (descriptions only)."""
    return DataResponse(data=FGMasterService(db).search_products(q, limit))


# ----------------------------------------------------------------- Workbench
@router.get("/emails/{email_id}/workbench", response_model=DataResponse[Dict[str, Any]])
def email_workbench(
    email_id: int,
    current: RequireUser,
    db: DbSession,
    attachment_id: Optional[int] = Query(None, ge=1),
    refresh: bool = Query(False),
) -> DataResponse[Dict[str, Any]]:
    service = ProductWorkbenchService(db)
    _ensure_email_access(current, db, service.get_email(email_id))
    return DataResponse(data=service.workbench(email_id, attachment_id, refresh=refresh))


@router.get("/emails/{email_id}/preview", response_model=DataResponse[Dict[str, Any]])
def email_standardized_preview(
    email_id: int,
    current: RequireUser,
    db: DbSession,
    attachment_id: Optional[int] = Query(None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, pattern="^(AUTO_MATCHED|NEEDS_REVIEW|UNMAPPED)$"),
    search: Optional[str] = Query(None, max_length=100),
) -> DataResponse[Dict[str, Any]]:
    service = ProductWorkbenchService(db)
    _ensure_email_access(current, db, service.get_email(email_id))
    return DataResponse(
        data=service.full_preview(
            email_id, attachment_id, page=page, page_size=page_size, status=status, search=search
        )
    )


@router.post("/approve", response_model=DataResponse[Dict[str, Any]])
def approve_products(
    payload: ProductApprovalRequest,
    current: RequireUser,
    db: DbSession,
) -> DataResponse[Dict[str, Any]]:
    workbench = ProductWorkbenchService(db)
    email = workbench.get_email(payload.email_id)
    _ensure_email_access(current, db, email)
    snap = workbench.snapshot(email, payload.attachment_id)
    known = {clean_display(r.get("product")).casefold() for r in (snap.rows or [])}
    unknown = [i.raw_product for i in payload.items if clean_display(i.raw_product).casefold() not in known]
    if unknown:
        raise ValidationAppError(
            "Some products are not part of this attachment's extracted data.",
            details={"products": unknown[:20]},
        )
    distributor = workbench.resolve_distributor(email)
    result = ProductLearningService(db).approve(
        [i.model_dump() for i in payload.items],
        current,
        distributor_id=distributor["id"] if distributor else None,
        email_id=email.id,
        attachment_id=snap.attachment_id,
    )
    return DataResponse(data=result, message=f"{result['approved']} product mapping(s) approved.")
