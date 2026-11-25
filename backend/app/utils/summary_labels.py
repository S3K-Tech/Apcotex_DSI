"""Report footer labels that ERP exports place in the customer column.

Only explicitly confirmed footers are listed. Customer names that merely start
with or contain "Total" (for example "Total Rubber Co") are real customers.
"""

from __future__ import annotations

import re

# Tally Stock Item Register footer: "Totals as per 'Default' valuation :".
TALLY_VALUATION_FOOTER_PATTERN = r"^\s*totals?\s+as\s+per\s"
TALLY_VALUATION_FOOTER_RE = re.compile(TALLY_VALUATION_FOOTER_PATTERN, re.IGNORECASE)

# Matrix / register summary rows that land in the customer column
# (e.g. "Altek Total Sales", "Party Total", "Grand Total").
_CUSTOMER_SUMMARY_RE = re.compile(
    r"^(grand\s+|sub\s+)?totals?\b|\btotal\s+(sales|qty|quantity|amount|value)\b|\btotals?\s*$",
    re.IGNORECASE,
)


def is_report_footer_label(value: object) -> bool:
    return bool(TALLY_VALUATION_FOOTER_RE.match(str(value or "")))


def is_customer_summary_label(value: object) -> bool:
    """True for ERP summary/footer text that must never count as a customer."""
    text = str(value or "").strip()
    if not text:
        return False
    if is_report_footer_label(text):
        return True
    return bool(_CUSTOMER_SUMMARY_RE.search(text))
