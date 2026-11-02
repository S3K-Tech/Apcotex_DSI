"""FG Master upload, validation, versioning, diff and publish (Super Admin)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import load_workbook
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.dependencies.rbac import RequestUser
from app.enums import AuditAction, AuditModule
from app.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.fg_master import FGMasterProduct, FGMasterVersion
from app.models.product_alias_mapping import ProductAliasMapping
from app.schemas.audit import AuditTrailCreate
from app.services.audit_service import AuditService
from app.services.product_master.normalize import (
    MAX_DESCRIPTION_LENGTH,
    clean_display,
    normalize_key,
    role_value,
)

logger = get_logger(__name__)

TARGET_HEADER = "MATERIAL DESCRIPTION"
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
MAX_MASTER_FILE_BYTES = 10 * 1024 * 1024
MAX_MASTER_ROWS = 100_000
_HEADER_SCAN_ROWS = 25
_SAMPLE_LIMIT = 50
_PUBLISH_LOCK_KEY = 7_302_118_001

STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_DISCARDED = "discarded"


@dataclass
class ParsedMaster:
    sheet_name: str
    header_row: int
    total_rows: int = 0
    valid: List[Tuple[str, str]] = field(default_factory=list)  # (display, key)
    duplicates: List[Dict[str, Any]] = field(default_factory=list)
    invalid: List[Dict[str, Any]] = field(default_factory=list)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return clean_display(value)


def validate_master_file(file_name: str, data: bytes) -> None:
    ext = Path(file_name or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationAppError("FG Master must be an .xlsx or .xlsm Excel workbook.")
    if not data:
        raise ValidationAppError("The uploaded file is empty.")
    if len(data) > MAX_MASTER_FILE_BYTES:
        raise ValidationAppError(
            f"FG Master file exceeds the {MAX_MASTER_FILE_BYTES // (1024 * 1024)} MB limit."
        )
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValidationAppError("The uploaded file is not a valid Excel workbook.")


def parse_master_workbook(data: bytes) -> ParsedMaster:
    """Read only the ``Material Description`` column of the FG Master workbook."""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # corrupt / encrypted / not a workbook
        raise ValidationAppError("The Excel workbook could not be opened.") from exc

    try:
        hits: List[Tuple[str, int, int]] = []
        seen_headers: List[str] = []
        for ws in wb.worksheets:
            for r_idx, row in enumerate(
                ws.iter_rows(min_row=1, max_row=_HEADER_SCAN_ROWS, values_only=True), start=1
            ):
                for c_idx, value in enumerate(row):
                    label = _cell_text(value)
                    if not label:
                        continue
                    if len(seen_headers) < 40:
                        seen_headers.append(label)
                    if normalize_key(label) == TARGET_HEADER:
                        hits.append((ws.title, r_idx, c_idx))
        if not hits:
            raise ValidationAppError(
                "Required column 'Material Description' was not found in the workbook.",
                details={"headers_seen": sorted(set(seen_headers))[:40]},
            )
        if len(hits) > 1:
            raise ValidationAppError(
                "The workbook has more than one 'Material Description' column; "
                "keep exactly one and upload again.",
                details={"locations": [{"sheet": s, "row": r, "column": c + 1} for s, r, c in hits]},
            )

        sheet_name, header_row, col = hits[0]
        parsed = ParsedMaster(sheet_name=sheet_name, header_row=header_row)
        seen: Dict[str, int] = {}
        ws = wb[sheet_name]
        for r_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            if not any(v is not None and str(v).strip() for v in row):
                continue
            parsed.total_rows += 1
            if parsed.total_rows > MAX_MASTER_ROWS:
                raise ValidationAppError(f"FG Master exceeds the {MAX_MASTER_ROWS} row limit.")
            raw = row[col] if col < len(row) else None
            display = _cell_text(raw)
            key = normalize_key(display)
            reason = None
            if not display:
                reason = "Material Description is empty"
            elif len(display) > MAX_DESCRIPTION_LENGTH:
                reason = f"Longer than {MAX_DESCRIPTION_LENGTH} characters"
            elif not key or not any(ch.isalpha() for ch in key):
                reason = "No alphabetic characters"
            if reason:
                parsed.invalid.append({"row": r_idx, "value": display[:120], "reason": reason})
                continue
            if key in seen:
                parsed.duplicates.append({"row": r_idx, "value": display, "first_row": seen[key]})
                continue
            seen[key] = r_idx
            parsed.valid.append((display, key))
        if not parsed.valid:
            raise ValidationAppError(
                "No valid Material Description values were found.",
                details={"invalid_count": len(parsed.invalid)},
            )
        return parsed
    finally:
        wb.close()


def version_to_dict(v: Optional[FGMasterVersion]) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    return {
        "id": v.id,
        "version_number": v.version_number,
        "status": v.status,
        "file_name": v.file_name,
        "file_size": v.file_size,
        "sheet_name": v.sheet_name,
        "total_rows": v.total_rows,
        "valid_count": v.valid_count,
        "duplicate_count": v.duplicate_count,
        "invalid_count": v.invalid_count,
        "uploaded_by_name": v.uploaded_by_name,
        "uploaded_at": v.created_at.isoformat() if v.created_at else None,
        "published_by_name": v.published_by_name,
        "published_at": v.published_at.isoformat() if v.published_at else None,
    }


class FGMasterService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ reads
    def active_version(self) -> Optional[FGMasterVersion]:
        return self.db.scalar(
            select(FGMasterVersion).where(FGMasterVersion.status == STATUS_ACTIVE).limit(1)
        )

    def latest_draft(self) -> Optional[FGMasterVersion]:
        return self.db.scalar(
            select(FGMasterVersion)
            .where(FGMasterVersion.status == STATUS_DRAFT)
            .order_by(FGMasterVersion.id.desc())
            .limit(1)
        )

    def get_version(self, version_id: int) -> FGMasterVersion:
        v = self.db.get(FGMasterVersion, version_id)
        if v is None:
            raise NotFoundError("FG Master version not found")
        return v

    def status(self) -> Dict[str, Any]:
        active = self.active_version()
        draft = self.latest_draft()
        return {
            "configured": active is not None,
            "active": version_to_dict(active),
            "draft": version_to_dict(draft),
            "draft_diff": self.diff(draft) if draft is not None else None,
        }

    def list_versions(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.db.scalars(
            select(FGMasterVersion).order_by(FGMasterVersion.version_number.desc()).limit(limit)
        )
        return [version_to_dict(v) for v in rows]

    def _keys_for(self, version_id: int) -> Dict[str, str]:
        rows = self.db.execute(
            select(FGMasterProduct.normalized_description, FGMasterProduct.material_description).where(
                FGMasterProduct.version_id == version_id
            )
        ).all()
        return {k: d for k, d in rows}

    def search_products(self, query: str = "", limit: int = 20) -> Dict[str, Any]:
        active = self.active_version()
        if active is None:
            return {"configured": False, "items": []}
        limit = max(1, min(int(limit or 20), 100))
        stmt = select(FGMasterProduct.material_description).where(
            FGMasterProduct.version_id == active.id, FGMasterProduct.is_active.is_(True)
        )
        q = clean_display(query)
        if q:
            key = normalize_key(q)
            stmt = stmt.where(
                FGMasterProduct.material_description.ilike(f"%{q}%")
                | FGMasterProduct.normalized_description.like(f"%{key}%")
            )
        stmt = stmt.order_by(func.length(FGMasterProduct.material_description), FGMasterProduct.material_description)
        return {"configured": True, "items": list(self.db.scalars(stmt.limit(limit)))}

    def diff(self, draft: FGMasterVersion) -> Dict[str, Any]:
        active = self.active_version()
        new_keys = self._keys_for(draft.id)
        old_keys = self._keys_for(active.id) if active is not None and active.id != draft.id else {}
        added = sorted(k for k in new_keys if k not in old_keys)
        removed = sorted(k for k in old_keys if k not in new_keys)
        unchanged = len(new_keys) - len(added)
        removed_set = set(removed)
        affected: List[Dict[str, Any]] = []
        if removed_set:
            for m in self.db.scalars(
                select(ProductAliasMapping).where(
                    ProductAliasMapping.is_active.is_(True),
                    ProductAliasMapping.status == "APPROVED",
                )
            ):
                if normalize_key(m.normalized_product_name) in removed_set:
                    affected.append(
                        {
                            "mapping_id": m.id,
                            "original_product_name": m.original_product_name,
                            "standard_product": m.normalized_product_name,
                            "scope": m.scope,
                            "distributor_id": m.distributor_id,
                        }
                    )
        return {
            "against_version": active.version_number if active is not None else None,
            "new_count": len(added),
            "removed_count": len(removed),
            "unchanged_count": unchanged,
            "new_sample": [new_keys[k] for k in added[:_SAMPLE_LIMIT]],
            "removed_sample": [old_keys[k] for k in removed[:_SAMPLE_LIMIT]],
            "affected_mappings_count": len(affected),
            "affected_mappings": affected[:_SAMPLE_LIMIT],
        }

    # ----------------------------------------------------------------- writes
    def _lock(self) -> None:
        self.db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PUBLISH_LOCK_KEY})

    def upload_draft(self, file_name: str, data: bytes, current: RequestUser) -> Dict[str, Any]:
        safe_name = Path(file_name or "fg_master.xlsx").name[:255]
        validate_master_file(safe_name, data)
        parsed = parse_master_workbook(data)
        self._lock()

        for old in self.db.scalars(select(FGMasterVersion).where(FGMasterVersion.status == STATUS_DRAFT)):
            old.status = STATUS_DISCARDED
        next_no = int(self.db.scalar(select(func.coalesce(func.max(FGMasterVersion.version_number), 0))) or 0) + 1
        version = FGMasterVersion(
            version_number=next_no,
            status=STATUS_DRAFT,
            file_name=safe_name,
            file_sha256=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            sheet_name=parsed.sheet_name[:255],
            total_rows=parsed.total_rows,
            valid_count=len(parsed.valid),
            duplicate_count=len(parsed.duplicates),
            invalid_count=len(parsed.invalid),
            uploaded_by=current.user_id,
            uploaded_by_name=current.name,
        )
        self.db.add(version)
        self.db.flush()
        self.db.bulk_insert_mappings(
            FGMasterProduct,
            [
                {"version_id": version.id, "material_description": d, "normalized_description": k, "is_active": True}
                for d, k in parsed.valid
            ],
        )
        self.db.flush()
        self.audit.log(
            AuditTrailCreate(
                user_name=current.name,
                user_id=current.user_id,
                user_role=role_value(current),
                action=AuditAction.UPLOADED,
                details=(
                    f"FG Master draft v{next_no} uploaded | file={safe_name} | valid={len(parsed.valid)} | "
                    f"duplicates={len(parsed.duplicates)} | invalid={len(parsed.invalid)}"
                ),
                entity_type="fg_master_version",
                entity_id=str(version.id),
                module=AuditModule.MASTER_DATA.value,
                status="Success",
            )
        )
        logger.info(
            "FG Master draft uploaded | version={} | valid={} | duplicates={} | invalid={}",
            next_no, len(parsed.valid), len(parsed.duplicates), len(parsed.invalid),
        )
        return {
            "version": version_to_dict(version),
            "duplicates_sample": parsed.duplicates[:_SAMPLE_LIMIT],
            "invalid_sample": parsed.invalid[:_SAMPLE_LIMIT],
            "diff": self.diff(version),
        }

    def publish(self, version_id: int, current: RequestUser) -> Dict[str, Any]:
        self._lock()
        version = self.get_version(version_id)
        if version.status != STATUS_DRAFT:
            raise ConflictError(f"Only a draft can be published (this version is {version.status}).")
        diff = self.diff(version)
        previous = self.active_version()
        if previous is not None:
            previous.status = STATUS_SUPERSEDED
            self.db.flush()
        version.status = STATUS_ACTIVE
        version.published_by = current.user_id
        version.published_by_name = current.name
        version.published_at = datetime.now(timezone.utc)
        self.db.flush()
        self.audit.log(
            AuditTrailCreate(
                user_name=current.name,
                user_id=current.user_id,
                user_role=role_value(current),
                action="Published",
                details=(
                    f"FG Master v{version.version_number} published | products={version.valid_count} | "
                    f"new={diff['new_count']} | removed={diff['removed_count']} | "
                    f"replaced=v{previous.version_number if previous else '-'}"
                ),
                entity_type="fg_master_version",
                entity_id=str(version.id),
                module=AuditModule.MASTER_DATA.value,
                status="Success",
                extra_metadata={"affected_mappings": diff["affected_mappings_count"]},
            )
        )
        return {"version": version_to_dict(version), "diff": diff}

    def discard(self, version_id: int, current: RequestUser) -> Dict[str, Any]:
        self._lock()
        version = self.get_version(version_id)
        if version.status != STATUS_DRAFT:
            raise ConflictError("Only a draft can be discarded.")
        version.status = STATUS_DISCARDED
        self.db.flush()
        self.audit.log(
            AuditTrailCreate(
                user_name=current.name,
                user_id=current.user_id,
                user_role=role_value(current),
                action=AuditAction.UPDATED,
                details=f"FG Master draft v{version.version_number} discarded",
                entity_type="fg_master_version",
                entity_id=str(version.id),
                module=AuditModule.MASTER_DATA.value,
                status="Success",
            )
        )
        return {"version": version_to_dict(version)}
