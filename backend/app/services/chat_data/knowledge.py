"""Approved business knowledge for Chat with Data (glossary, metrics, rules).

These texts are embedded by the sync script and used as interpretation
context. They describe how figures are computed; they hold no sales data.
"""

from __future__ import annotations

from typing import List, Tuple

KNOWLEDGE_DOCUMENTS: List[Tuple[str, str]] = [
    (
        "metric_quantity_mt",
        "Quantity / volume / sales / offtake means total quantity in metric tonnes (MT). "
        "KG rows are divided by 1000. Rows already marked MT are used as stored. "
        "Piece-count units (Units, Nos, Pcs) are not weight and contribute 0 MT.",
    ),
    (
        "metric_customer_count",
        "Customer count is the number of distinct customer names after trimming, "
        "collapsing spaces and ignoring letter case.",
    ),
    (
        "metric_product_count",
        "Product count is the number of distinct canonical products. Labels that differ only "
        "by case, spacing or a hyphen in the grade code (N-385 vs N385) are one product.",
    ),
    (
        "rule_financial_year",
        "The financial year runs April to March. FY 2026-27 is April 2026 to March 2027. "
        "Q1 is April–June, Q2 July–September, Q3 October–December, Q4 January–March. "
        "Every question must name an explicit financial year.",
    ),
    (
        "rule_monthly_data",
        "Monthly trends use only rows reported with a calendar month. Quarterly or annual "
        "submissions are never split into estimated months.",
    ),
    (
        "rule_no_revenue",
        "The data contains quantities only. There is no price, value, revenue or margin.",
    ),
    (
        "rule_scope",
        "Answers include only the segments or distributors assigned to the signed-in user. "
        "Administrators see all data.",
    ),
    (
        "term_distributor",
        "A distributor (also called dealer, channel partner or company) is the business that "
        "submitted the sales report. Rankings by distributor use the distributor company name.",
    ),
    (
        "term_customer",
        "A customer is the end buyer named on a distributor's sales row.",
    ),
    (
        "term_segment",
        "Segments are Paper, Carpet, Construction, Rubber and Gloves. NVC, HSR, NBR and Latex "
        "product families belong to Rubber.",
    ),
    (
        "question_types",
        "Supported questions: totals, top or bottom customers, products or distributors, "
        "breakdowns by segment, quarter or month, comparisons between two periods, monthly "
        "trends, and which customers or products contributed to a change.",
    ),
]
