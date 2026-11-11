"""Apply approved / exact customer standardization to rows about to be imported."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.customer_standardization.matching import AUTO_MATCHED, CustomerMatch, CustomerMatcher
from app.services.customer_standardization.normalize import MAX_CUSTOMER_NAME_LENGTH, clean_display

logger = get_logger(__name__)


def apply_customer_standardization(
    db: Session,
    rows: List[Dict[str, Any]],
    *,
    distributor_id: Optional[int],
) -> Dict[str, Any]:
    """
    Mutate ``rows`` in place for a NEW import only.

    AUTO_MATCHED rows (approved mapping or exact established name) get the
    standard name in ``customer_name``; every row keeps the distributor's text in
    ``original_customer_name``. Rows needing review or unmapped keep their raw
    name. Never raises: a failure leaves the rows exactly as extracted.
    """
    summary = {"auto_matched": 0, "needs_review": 0, "unmapped": 0}
    try:
        with db.begin_nested():
            matcher = CustomerMatcher.load(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Customer standardization skipped | reason={}", type(exc).__name__)
        return summary

    cache: Dict[str, CustomerMatch] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = clean_display(row.get("customer_name") or row.get("customer"))
        if not raw:
            continue
        result = cache.get(raw)
        if result is None:
            result = matcher.match(raw, distributor_id)
            cache[raw] = result
        row["original_customer_name"] = raw[:MAX_CUSTOMER_NAME_LENGTH]
        if result.status == AUTO_MATCHED and result.suggested and len(result.suggested) <= MAX_CUSTOMER_NAME_LENGTH:
            row["customer_name"] = result.suggested
            if "customer" in row:
                row["customer"] = result.suggested
            row["customer_standardization_status"] = AUTO_MATCHED
            row["customer_mapping_id"] = result.mapping_id
            summary["auto_matched"] += 1
        else:
            row["customer_standardization_status"] = result.status if result.status != AUTO_MATCHED else "UNMAPPED"
            row["customer_mapping_id"] = None
            if result.status == "NEEDS_REVIEW":
                summary["needs_review"] += 1
            else:
                summary["unmapped"] += 1
    return summary
