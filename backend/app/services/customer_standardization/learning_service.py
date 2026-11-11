"""
Approval + continuous learning for customer mappings.

Rules:
- Every approval is stored as a DISTRIBUTOR mapping for the distributor in context.
- An existing distributor mapping with a different value is never replaced
  silently: the item is returned as a conflict unless the caller explicitly
  sets ``replace_existing`` (the old mapping is then superseded, with history).
- A GLOBAL mapping is created only when none exists for the same key. An
  existing global mapping is never overwritten: a differing approval stays
  distributor-specific and is recorded as GLOBAL_CONFLICT.
- No global mapping redirects an exact customer-master name to another customer.
- Approvals are serialized per key with a transaction-scoped advisory lock;
  partial unique indexes back this up.
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
from app.models.customer_alias_mapping import CustomerAliasMapping
from app.models.customer_mapping_history import CustomerMappingHistory
from app.models.distributor import Distributor
from app.schemas.audit import AuditTrailCreate
from app.services.audit_service import AuditService
from app.services.customer_standardization.matching import CustomerMatcher
from app.services.customer_standardization.normalize import (
    MAX_CUSTOMER_NAME_LENGTH,
    clean_display,
    mapping_key,
    role_value,
)

SCOPE_GLOBAL = "GLOBAL"
SCOPE_DISTRIBUTOR = "DISTRIBUTOR"
MAX_BATCH = 500


def _same(a: Optional[str], b: Optional[str]) -> bool:
    return clean_display(a).casefold() == clean_display(b).casefold()


class CustomerLearningService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)

    def _lock_key(self, key: str) -> None:
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"customer-mapping:{key}"}
        )

    def _active(self, key: str, distributor_id: Optional[int]) -> List[CustomerAliasMapping]:
        stmt = select(CustomerAliasMapping).where(
            CustomerAliasMapping.is_active.is_(True),
            CustomerAliasMapping.status == "APPROVED",
            CustomerAliasMapping.normalized_original_name == key,
        )
        if distributor_id is None:
            stmt = stmt.where(CustomerAliasMapping.distributor_id.is_(None))
        else:
            stmt = stmt.where(CustomerAliasMapping.distributor_id == distributor_id)
        return list(self.db.scalars(stmt.order_by(CustomerAliasMapping.id)))

    def _history(self, **kwargs: Any) -> None:
        self.db.add(CustomerMappingHistory(**kwargs))

    def _prepare(self, items: List[Dict[str, Any]], matcher: CustomerMatcher) -> Dict[str, Dict[str, Any]]:
        prepared: Dict[str, Dict[str, Any]] = {}
        for item in items:
            raw = clean_display(item.get("raw_customer"))
            standard = clean_display(item.get("standard_customer"))
            key = mapping_key(raw)
            if not key:
                raise ValidationAppError("Raw customer name is empty.")
            if not mapping_key(standard):
                raise ValidationAppError(f"Enter a standardized customer name for '{raw}' before approving.")
            if len(raw) > MAX_CUSTOMER_NAME_LENGTH or len(standard) > MAX_CUSTOMER_NAME_LENGTH:
                raise ValidationAppError(
                    f"Customer names must be at most {MAX_CUSTOMER_NAME_LENGTH} characters."
                )
            established = matcher.established(standard)
            final = established[0] if established else standard
            previous = prepared.get(key)
            if previous and not _same(previous["standard"], final):
                raise ValidationAppError(f"'{raw}' was given two different standardized customers in this request.")
            prepared[key] = {
                "raw": raw,
                "key": key,
                "standard": final,
                "is_new": established is None,
                "confidence": item.get("confidence"),
                "replace": bool(item.get("replace_existing")),
            }
        return prepared

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
            raise ValidationAppError("Select at least one customer to approve.")
        if len(items) > MAX_BATCH:
            raise ValidationAppError(f"Approve at most {MAX_BATCH} customers at a time.")
        if distributor_id is None:
            raise ValidationAppError(
                "The distributor for this email is not resolved yet. Resolve it before approving customers."
            )
        if self.db.get(Distributor, int(distributor_id)) is None:
            raise NotFoundError("Distributor not found")

        matcher = CustomerMatcher.load(self.db)
        prepared = self._prepare(items, matcher)

        now = datetime.now(timezone.utc)
        dist_id = int(distributor_id)
        results: List[Dict[str, Any]] = []
        common = {
            "changed_by": current.user_id,
            "changed_by_name": current.name,
            "email_id": email_id,
            "attachment_id": attachment_id,
        }
        try:
            for key in sorted(prepared):
                p = prepared[key]
                self._lock_key(key)
                outcome: Dict[str, Any] = {
                    "raw_customer": p["raw"],
                    "standard_customer": p["standard"],
                    "is_new_customer": p["is_new"],
                }

                existing = self._active(key, dist_id)
                if existing and all(_same(m.standardized_customer_name, p["standard"]) for m in existing):
                    outcome["distributor_action"] = "unchanged"
                    outcome["mapping_id"] = existing[0].id
                elif existing and not p["replace"]:
                    outcome["distributor_action"] = "conflict"
                    outcome["mapping_id"] = existing[0].id
                    outcome["existing_customer"] = existing[0].standardized_customer_name
                    self._history(
                        mapping_id=existing[0].id,
                        original_customer_name=p["raw"],
                        normalized_original_name=key,
                        previous_value=existing[0].standardized_customer_name,
                        new_value=p["standard"],
                        scope=SCOPE_DISTRIBUTOR,
                        distributor_id=dist_id,
                        action="APPROVAL_CONFLICT",
                        reason="Existing approved mapping kept; replacement requires confirmation",
                        **common,
                    )
                    results.append(outcome)
                    continue
                else:
                    previous_value = existing[0].standardized_customer_name if existing else None
                    for old in existing:
                        old.is_active = False
                        old.status = "SUPERSEDED"
                    self.db.flush()
                    mapping = CustomerAliasMapping(
                        distributor_id=dist_id,
                        original_customer_name=p["raw"],
                        normalized_original_name=key,
                        standardized_customer_name=p["standard"],
                        scope=SCOPE_DISTRIBUTOR,
                        source="workbench",
                        status="APPROVED",
                        is_active=True,
                        is_new_customer=p["is_new"],
                        confidence=_float_or_none(p["confidence"]),
                        created_by=current.user_id,
                        approved_by=current.user_id,
                        approved_by_name=current.name,
                        approved_at=now,
                    )
                    self.db.add(mapping)
                    self.db.flush()
                    outcome["distributor_action"] = "updated" if existing else "created"
                    outcome["mapping_id"] = mapping.id
                    self._history(
                        mapping_id=mapping.id,
                        original_customer_name=p["raw"],
                        normalized_original_name=key,
                        previous_value=previous_value,
                        new_value=p["standard"],
                        scope=SCOPE_DISTRIBUTOR,
                        distributor_id=dist_id,
                        action="UPDATED" if existing else "CREATED",
                        reason="New customer name" if p["is_new"] else None,
                        **common,
                    )

                global_rows = self._active(key, None)
                master_name = matcher.is_master_name(p["raw"])
                if global_rows:
                    if any(_same(g.standardized_customer_name, p["standard"]) for g in global_rows):
                        outcome["global_action"] = "unchanged"
                    else:
                        outcome["global_action"] = "conflict_kept_distributor"
                        self._history(
                            mapping_id=global_rows[0].id,
                            original_customer_name=p["raw"],
                            normalized_original_name=key,
                            previous_value=global_rows[0].standardized_customer_name,
                            new_value=p["standard"],
                            scope=SCOPE_GLOBAL,
                            distributor_id=dist_id,
                            action="GLOBAL_CONFLICT",
                            reason="Global mapping kept; approval stored for this distributor only",
                            **common,
                        )
                elif master_name and not _same(master_name, p["standard"]):
                    outcome["global_action"] = "skipped_master_exact"
                else:
                    g = CustomerAliasMapping(
                        distributor_id=None,
                        original_customer_name=p["raw"],
                        normalized_original_name=key,
                        standardized_customer_name=p["standard"],
                        scope=SCOPE_GLOBAL,
                        source="workbench",
                        status="APPROVED",
                        is_active=True,
                        is_new_customer=p["is_new"],
                        confidence=_float_or_none(p["confidence"]),
                        created_by=current.user_id,
                        approved_by=current.user_id,
                        approved_by_name=current.name,
                        approved_at=now,
                    )
                    self.db.add(g)
                    self.db.flush()
                    outcome["global_action"] = "created"
                    self._history(
                        mapping_id=g.id,
                        original_customer_name=p["raw"],
                        normalized_original_name=key,
                        previous_value=None,
                        new_value=p["standard"],
                        scope=SCOPE_GLOBAL,
                        distributor_id=dist_id,
                        action="CREATED",
                        **common,
                    )
                results.append(outcome)
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Another user approved one of these customers at the same time. Refresh and try again."
            ) from exc

        saved = sum(1 for r in results if r["distributor_action"] in ("created", "updated"))
        conflicts = [r for r in results if r["distributor_action"] == "conflict"]
        global_conflicts = sum(1 for r in results if r.get("global_action") == "conflict_kept_distributor")
        self.audit.log(
            AuditTrailCreate(
                user_name=current.name,
                user_id=current.user_id,
                user_role=role_value(current),
                action="Customer Mapping Approved",
                details=(
                    f"Customer mappings approved | distributor_id={distributor_id} | email_id={email_id} | "
                    f"attachment_id={attachment_id} | items={len(results)} | saved={saved} | "
                    f"conflicts={len(conflicts)} | global_conflicts={global_conflicts}"
                ),
                entity_type="customer_mapping",
                entity_id=str(email_id or ""),
                module=AuditModule.MASTER_DATA.value,
                status="Success",
            )
        )
        return {
            "approved": len(results) - len(conflicts),
            "saved": saved,
            "conflicts": conflicts,
            "global_conflicts": global_conflicts,
            "results": results,
        }


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
