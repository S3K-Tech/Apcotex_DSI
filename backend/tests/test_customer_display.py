"""Approved customer identity for Visualizations / Analytics (no heuristic merge)."""

from __future__ import annotations

from app.services.customer_standardization.display import (
    canonical_customers,
    expand_customer_filter,
    merge_customer_quantities,
    normalize_customer_key,
)


def test_normalize_keeps_distinct_approved_spellings():
    """Punctuation / spacing variants remain separate approved identities."""
    assert normalize_customer_key("BSK ENTERPRISES") != normalize_customer_key(
        "B.S.K ENTERPRISES"
    )
    assert normalize_customer_key("SS Polymers") != normalize_customer_key("S.S POLYMERS")
    assert normalize_customer_key("  Acme Co  ") == "Acme Co"


def test_canonical_customers_exact_dedupe_excludes_blank():
    names = [
        "BSK ENTERPRISES",
        "B.S.K ENTERPRISES",
        "BSK ENTERPRISES",
        "",
        "   ",
        None,  # type: ignore[arg-type]
        "ALP Nishikawa Company Pvt Ltd",
    ]
    out = canonical_customers([n for n in names if n is not None])
    assert out == [
        "ALP Nishikawa Company Pvt Ltd",
        "B.S.K ENTERPRISES",
        "BSK ENTERPRISES",
    ]


def test_expand_customer_filter_exact_only():
    raw = [
        "FAVOURITE SAFETY PRODUCTS",
        "FAVOURITE SAFETY PRODUCTS.",
        "Other Co",
    ]
    matches = expand_customer_filter("FAVOURITE SAFETY PRODUCTS", raw)
    assert matches == ["FAVOURITE SAFETY PRODUCTS"]


def test_merge_customer_quantities_does_not_merge_distinct_approved():
    rows = [
        {"customer": "BSK ENTERPRISES", "kg": 10.0},
        {"customer": "B.S.K ENTERPRISES", "kg": 5.5},
        {"customer": "Others", "kg": 1.0},
    ]
    merged = merge_customer_quantities(rows)
    assert len(merged) == 3
    by_name = {r["customer"]: float(r["kg"]) for r in merged}
    assert by_name["BSK ENTERPRISES"] == 10.0
    assert by_name["B.S.K ENTERPRISES"] == 5.5
    assert by_name["Others"] == 1.0
