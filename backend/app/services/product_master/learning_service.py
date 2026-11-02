"""
Approval + continuous learning for product mappings.

Rules:
- Every approval is stored as a DISTRIBUTOR mapping for the distributor in context.
  A later correction by the same distributor supersedes its own previous mapping.
- A GLOBAL mapping is created only when none exists for the same normalized
  original name. An existing global mapping is never overwritten: a conflicting
  correction stays distributor-specific and is recorded as GLOBAL_CONFLICT.
- No global mapping is created that would redirect an exact FG Master
  description to a different product.
- Approvals are serialized per normalized name with a transaction-scoped
  advisory lock; partial unique indexes back this up.
- Mappings never touch sales_records (future imports only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies.rbac import RequestUser
from app.enums import AuditModule
from app.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.distributor import Distributor
from app.models.product_alias_mapping import ProductAliasMapping
from app.models.product_mapping_history import ProductMappingHistory
from app.schemas.audit import AuditTrailCreate
from app.services.audit_service import AuditService
from app.services.product_master.matching import ProductMatcher
from app.services.product_master.normalize import (
    MAX_APPLIED_PRODUCT_LENGTH,
    clean_display,
    compact_key,
    normalize_key,
    role_value,
)

SCOPE_GLOBAL = "GLOBAL"
SCOPE_DISTRIBUTOR = "DISTRIBUTOR"
MAX_BATCH = 500


class ProductLearningService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)

    def _lock_key(self, key: str) -> None:
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"product-mapping:{key}"}
        )

    def _active(self, key: str, distributor_id: Optional[int]) -> List[ProductAliasMapping]:
        stmt = select(ProductAliasMapping).where(
            ProductAliasMapping.is_active.is_(True),
            ProductAliasMapping.status == "APPROVED",
            ProductAliasMapping.normalized_original_name == key,
        )
        if distributor_id is None:
            stmt = stmt.where(ProductAliasMapping.distributor_id.is_(None))
        else:
            stmt = stmt.where(ProductAliasMapping.distributor_id == distributor_id)
        return list(self.db.scalars(stmt.order_by(ProductAliasMapping.id)))

    def _history(self, **kwargs: Any) -> None:
        self.db.add(ProductMappingHistory(**kwargs))

    def approve(
        self,
        items: List[Dict[str, Any]],
        current: RequestUser,
        *,
        distributor_id: Optional[int],
        email_id: Optional[int] = None,
        attachment_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not items:
            raise ValidationAppError("Select at least one product to approve.")
        if len(items) > MAX_BATCH:
            raise ValidationAppError(f"Approve at most {MAX_BATCH} products at a time.")
        if distributor_id is None:
            raise ValidationAppError(
                "The distributor for this email is not resolved yet. Resolve it before approving products."
            )
        if self.db.get(Distributor, int(distributor_id)) is None:
            raise NotFoundError("Distributor not found")

        matcher = ProductMatcher.load(self.db)
        version_id = matcher.master_version.id if matcher.master_version is not None else None

        prepared: Dict[str, Dict[str, Any]] = {}
        for item in items:
            raw = clean_display(item.get("raw_product"))
            standard = clean_display(item.get("standard_product"))
            key = compact_key(raw)
            if not key:
                raise ValidationAppError("Raw product entry is empty.")
            if not normalize_key(standard):
                raise ValidationAppError(f"Choose a standardized product for '{raw}' before approving.")
            if len(standard) > MAX_APPLIED_PRODUCT_LENGTH:
                raise ValidationAppError(
                    f"Standardized product for '{raw}' is longer than {MAX_APPLIED_PRODUCT_LENGTH} characters."
                )
            if len(raw) > 255:
                raise ValidationAppError("Raw product entry is longer than 255 characters.")
            master_value = matcher.master_contains(standard)
            final = master_value or standard
            previous = prepared.get(key)
            if previous and normalize_key(previous["standard"]) != normalize_key(final):
                raise ValidationAppError(f"'{raw}' was given two different standardized products in this request.")
            prepared[key] = {
                "raw": raw,
                "key": key,
                "standard": final,
                "is_custom": master_value is None,
                "confidence": item.get("confidence"),
            }

        now = datetime.now(timezone.utc)
        results: List[Dict[str, Any]] = []
        common = {
            "changed_by": current.user_id,
            "changed_by_name": current.name,
            "email_id": email_id,
            "attachment_id": attachment_id,
            "master_version_id": version_id,
        }
        try:
            for key in sorted(prepared):
                p = prepared[key]
                self._lock_key(key)
                outcome: Dict[str, Any] = {"raw_product": p["raw"], "standard_product": p["standard"]}

                # Distributor scope
                existing = self._active(key, int(distributor_id))
                same = [m for m in existing if normalize_key(m.normalized_product_name) == normalize_key(p["standard"])]
                if same and len(existing) == len(same):
                    outcome["distributor_action"] = "unchanged"
                    outcome["mapping_id"] = same[0].id
                else:
                    previous_value = existing[0].normalized_product_name if existing else None
                    for old in existing:
                        old.is_active = False
                        old.status = "SUPERSEDED"
                    self.db.flush()
                    mapping = ProductAliasMapping(
                        distributor_id=int(distributor_id),
                        original_product_name=p["raw"],
                        normalized_original_name=key,
                        normalized_product_name=p["standard"],
                        source="workbench",
                        scope=SCOPE_DISTRIBUTOR,
                        status="APPROVED",
                        created_by=current.user_id,
                        approved_by=current.user_id,
                        approved_by_name=current.name,
                        approved_at=now,
                        confidence=_float_or_none(p["confidence"]),
                        master_version_id=version_id,
                        is_custom_value=p["is_custom"],
                        is_active=True,
                    )
                    self.db.add(mapping)
                    self.db.flush()
                    outcome["distributor_action"] = "updated" if existing else "created"
                    outcome["mapping_id"] = mapping.id
                    self._history(
                        mapping_id=mapping.id,
                        original_product_name=p["raw"],
                        normalized_original_name=key,
                        previous_value=previous_value,
                        new_value=p["standard"],
                        scope=SCOPE_DISTRIBUTOR,
                        distributor_id=int(distributor_id),
                        action="UPDATED" if existing else "CREATED",
                        reason="Custom value" if p["is_custom"] else None,
                        **common,
                    )

                # Global scope
                global_rows = self._active(key, None)
                exact_master = matcher.master_contains(p["raw"])
                if global_rows:
                    if any(normalize_key(g.normalized_product_name) == normalize_key(p["standard"]) for g in global_rows):
                        outcome["global_action"] = "unchanged"
                    else:
                        outcome["global_action"] = "conflict_kept_distributor"
                        self._history(
                            mapping_id=global_rows[0].id,
                            original_product_name=p["raw"],
                            normalized_original_name=key,
                            previous_value=global_rows[0].normalized_product_name,
                            new_value=p["standard"],
                            scope=SCOPE_GLOBAL,
                            distributor_id=int(distributor_id),
                            action="GLOBAL_CONFLICT",
                            reason="Global mapping kept; correction stored for this distributor only",
                            **common,
                        )
                elif exact_master and normalize_key(exact_master) != normalize_key(p["standard"]):
                    outcome["global_action"] = "skipped_master_exact"
                else:
                    g = ProductAliasMapping(
                        distributor_id=None,
                        original_product_name=p["raw"],
                        normalized_original_name=key,
                        normalized_product_name=p["standard"],
                        source="workbench",
                        scope=SCOPE_GLOBAL,
                        status="APPROVED",
                        created_by=current.user_id,
                        approved_by=current.user_id,
                        approved_by_name=current.name,
                        approved_at=now,
                        confidence=_float_or_none(p["confidence"]),
                        master_version_id=version_id,
                        is_custom_value=p["is_custom"],
                        is_active=True,
                    )
                    self.db.add(g)
                    self.db.flush()
                    outcome["global_action"] = "created"
                    self._history(
                        mapping_id=g.id,
                        original_product_name=p["raw"],
                        normalized_original_name=key,
                        previous_value=None,
                        new_value=p["standard"],
                        scope=SCOPE_GLOBAL,
                        distributor_id=int(distributor_id),
                        action="CREATED",
                        **common,
                    )
                results.append(outcome)
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Another user approved one of these products at the same time. Refresh and try again."
            ) from exc

        created = sum(1 for r in results if r["distributor_action"] in ("created", "updated"))
        conflicts = sum(1 for r in results if r.get("global_action") == "conflict_kept_distributor")
        self.audit.log(
            AuditTrailCreate(
                user_name=current.name,
                user_id=current.user_id,
                user_role=role_value(current),
                action="Product Mapping Approved",
                details=(
                    f"Product mappings approved | distributor_id={distributor_id} | email_id={email_id} | "
                    f"attachment_id={attachment_id} | items={len(results)} | saved={created} | "
                    f"global_conflicts={conflicts}"
                ),
                entity_type="product_mapping",
                entity_id=str(email_id or ""),
                module=AuditModule.MASTER_DATA.value,
                status="Success",
            )
        )
        return {"approved": len(results), "saved": created, "global_conflicts": conflicts, "results": results}


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
