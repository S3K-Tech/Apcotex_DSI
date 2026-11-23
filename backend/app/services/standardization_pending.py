"""Batch pending-approval counts for the Emails list (one call, no N+1).

Uses cached product-standardization snapshots + the same bucket rules as the
Product and Customer workbenches. Emails without a snapshot return
``available=False`` (counts null) so the list never triggers Excel re-parses.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.email_message import EmailMessage
from app.models.product_standardization_snapshot import ProductStandardizationSnapshot
from app.services.customer_standardization.matching import CustomerMatcher
from app.services.customer_standardization.workbench_service import _bucket as customer_bucket
from app.services.customer_standardization.workbench_service import raw_customer
from app.services.product_master.matching import ProductMatcher
from app.services.product_master.normalize import clean_display as clean_product
from app.services.product_master.workbench_service import ProductWorkbenchService
from app.services.product_master.workbench_service import _bucket as product_bucket

_MAX_IDS = 200


def _unique_raws(rows: Iterable[Dict[str, Any]], getter) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = getter(row)
        if not raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def _product_pending(rows: List[Dict[str, Any]], matcher: ProductMatcher, distributor_id: Optional[int]) -> int:
    pending = 0
    for raw in _unique_raws(rows, lambda r: clean_product(r.get("product"))):
        if product_bucket(matcher.match(raw, distributor_id)) in ("awaiting_review", "unmapped"):
            pending += 1
    return pending


def _customer_pending(
    rows: List[Dict[str, Any]], matcher: CustomerMatcher, distributor_id: Optional[int]
) -> int:
    pending = 0
    for raw in _unique_raws(rows, raw_customer):
        if customer_bucket(matcher.match(raw, distributor_id)) in ("needs_review", "unmapped"):
            pending += 1
    return pending


class StandardizationPendingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._workbench = ProductWorkbenchService(db)

    def counts_for_emails(
        self,
        email_ids: List[int],
        *,
        segments: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        ids = []
        seen: Set[int] = set()
        for raw in email_ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            if eid <= 0 or eid in seen:
                continue
            seen.add(eid)
            ids.append(eid)
            if len(ids) >= _MAX_IDS:
                break

        empty = [
            {
                "email_id": eid,
                "products_pending": None,
                "customers_pending": None,
                "available": False,
            }
            for eid in ids
        ]
        if not ids:
            return []

        emails = {
            e.id: e
            for e in self.db.scalars(
                select(EmailMessage)
                .where(EmailMessage.id.in_(ids), EmailMessage.is_deleted.is_(False))
                .options(selectinload(EmailMessage.attachments))
            ).all()
        }
        # Primary Excel attachment id per email (same order as workbench default).
        primary_att: Dict[int, int] = {}
        for eid, email in emails.items():
            atts = self._workbench._attachments(email)
            if atts:
                primary_att[eid] = int(atts[0].id)

        if not primary_att:
            return empty

        snaps = {
            (s.email_id, s.attachment_id): s
            for s in self.db.scalars(
                select(ProductStandardizationSnapshot).where(
                    ProductStandardizationSnapshot.email_id.in_(list(primary_att.keys())),
                    ProductStandardizationSnapshot.attachment_id.in_(list(primary_att.values())),
                )
            ).all()
        }

        product_matcher = ProductMatcher.load(self.db)
        customer_matcher = CustomerMatcher.load(self.db, segments=segments)

        out: List[Dict[str, Any]] = []
        for eid in ids:
            email = emails.get(eid)
            att_id = primary_att.get(eid) if email else None
            snap = snaps.get((eid, att_id)) if att_id is not None else None
            if email is None or snap is None:
                out.append(
                    {
                        "email_id": eid,
                        "products_pending": None,
                        "customers_pending": None,
                        "available": False,
                    }
                )
                continue
            distributor = self._workbench.resolve_distributor(email)
            distributor_id = distributor["id"] if distributor else None
            # Prefer distributor stamped on the snapshot when subject resolution fails.
            if distributor_id is None and snap.distributor_id is not None:
                distributor_id = int(snap.distributor_id)
            rows = [r for r in (snap.rows or []) if isinstance(r, dict)]
            out.append(
                {
                    "email_id": eid,
                    "products_pending": _product_pending(rows, product_matcher, distributor_id),
                    "customers_pending": _customer_pending(rows, customer_matcher, distributor_id),
                    "available": True,
                }
            )
        return out
