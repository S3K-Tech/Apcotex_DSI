"""Apply approved / exact-master product standardization to rows about to be imported."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.product_master.matching import AUTO_MATCHED, MatchResult, ProductMatcher
from app.services.product_master.normalize import MAX_APPLIED_PRODUCT_LENGTH, clean_display

logger = get_logger(__name__)


def apply_product_standardization(
    db: Session,
    rows: List[Dict[str, Any]],
    *,
    distributor_id: Optional[int],
) -> Dict[str, Any]:
    """
    Mutate ``rows`` in place for a NEW import only.

    AUTO_MATCHED rows get the standard description in ``product``; every row keeps
    the distributor's text in ``original_product_name``. Rows needing review or
    unmapped keep their raw product so nothing is lost. Never raises: a failure
    leaves the rows exactly as extracted.
    """
    summary = {"auto_matched": 0, "needs_review": 0, "unmapped": 0, "master_configured": False}
    try:
        with db.begin_nested():
            matcher = ProductMatcher.load(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Product standardization skipped | reason={}", type(exc).__name__)
        return summary
    summary["master_configured"] = matcher.master_configured

    cache: Dict[str, MatchResult] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = clean_display(row.get("product"))
        if not raw:
            continue
        result = cache.get(raw)
        if result is None:
            result = matcher.match(raw, distributor_id)
            cache[raw] = result
        row["original_product_name"] = raw[:255]
        if (
            result.status == AUTO_MATCHED
            and result.suggested
            and len(result.suggested) <= MAX_APPLIED_PRODUCT_LENGTH
        ):
            row["product"] = result.suggested
            row["product_standardization_status"] = AUTO_MATCHED
            row["product_mapping_id"] = result.mapping_id
            summary["auto_matched"] += 1
        else:
            row["product_standardization_status"] = result.status if result.status != AUTO_MATCHED else "UNMAPPED"
            row["product_mapping_id"] = None
            if result.status == "NEEDS_REVIEW":
                summary["needs_review"] += 1
            else:
                summary["unmapped"] += 1
    return summary
