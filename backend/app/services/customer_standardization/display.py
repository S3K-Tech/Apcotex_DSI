"""Approved customer identity for visualization/analytics.

After DB-side customer standardization, approved names are authoritative.
This module does NOT apply fuzzy matching, legal-form collapsing, or
punctuation merging — only trim/collapse whitespace and drop blanks.

Does NOT rewrite historical sales rows.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from app.services.customer_standardization.normalize import clean_display


def normalize_customer_key(name: str) -> str:
    """Exact approved identity key: trimmed display text (no heuristic merge)."""
    return clean_display(name)


def prefer_display_name(current: str | None, candidate: str) -> str:
    """Keep the first non-blank approved label (names are already authoritative)."""
    cand = clean_display(candidate)
    if not cand:
        return current or ""
    if not current:
        return cand
    return clean_display(current) or cand


def canonical_customers(raw_names: Sequence[str]) -> List[str]:
    """Distinct approved customer labels (blank / whitespace-only excluded)."""
    seen: Dict[str, str] = {}
    for raw in raw_names:
        name = clean_display(raw)
        if not name:
            continue
        # Preserve first-seen spelling; keys are exact trimmed text.
        seen.setdefault(name, name)
    return sorted(seen.values(), key=lambda s: s.casefold())


def expand_customer_filter(selected: str, all_raw: Sequence[str]) -> List[str]:
    """
    Resolve a filter selection to matching raw DB labels.

    Exact trimmed match only — does not expand across punctuation / legal-form
    variants of distinct approved customers.
    """
    selected_name = clean_display(selected)
    if not selected_name or selected_name.lower() == "all":
        return []
    matches = [
        clean_display(raw)
        for raw in all_raw
        if raw and clean_display(str(raw)) == selected_name
    ]
    return matches or [selected_name]


def merge_customer_quantities(
    rows: Sequence[Dict[str, object]],
    *,
    name_key: str = "customer",
    qty_key: str = "kg",
) -> List[Dict[str, object]]:
    """Sum quantities only for identical approved customer labels."""
    buckets: Dict[str, Dict[str, object]] = {}
    order: List[str] = []
    for row in rows:
        raw = clean_display(row.get(name_key) or "")
        if not raw or raw == "Others":
            key = "__others__" if raw == "Others" else ""
            if not key:
                continue
        else:
            key = raw
        if key not in buckets:
            buckets[key] = {
                name_key: raw if key != "__others__" else "Others",
                qty_key: float(row.get(qty_key) or 0),
            }
            order.append(key)
        else:
            buckets[key][qty_key] = float(buckets[key][qty_key] or 0) + float(
                row.get(qty_key) or 0
            )
    return [buckets[k] for k in order]
