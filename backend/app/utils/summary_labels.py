"""Report footer labels that ERP exports place in the customer column.

Only explicitly confirmed footers are listed. Customer names that merely start
with or contain "Total" (for example "Total Rubber Co") are real customers.
"""

from __future__ import annotations

import re

# Tally Stock Item Register footer: "Totals as per 'Default' valuation :".
TALLY_VALUATION_FOOTER_PATTERN = r"^\s*totals?\s+as\s+per\s"
TALLY_VALUATION_FOOTER_RE = re.compile(TALLY_VALUATION_FOOTER_PATTERN, re.IGNORECASE)


def is_report_footer_label(value: object) -> bool:
    return bool(TALLY_VALUATION_FOOTER_RE.match(str(value or "")))
