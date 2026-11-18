"""LLM executive summary: grounded payload, prompt, parsing and number verification."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.core.logging import get_logger
from app.exceptions import AppException

logger = get_logger(__name__)

SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("executive_overview", "Executive Overview"),
    ("sales_performance", "Sales Performance Analysis"),
    ("product_performance", "Product Performance"),
    ("customer_performance", "Customer Performance"),
    ("distributor_performance", "Distributor Performance"),
    ("negative_observations", "Negative Observations and Business Risks"),
    ("positive_observations", "Positive Observations and Achievements"),
    ("recommendations", "Management Recommendations and Action Items"),
    ("closing_summary", "Closing Management Summary"),
)
SECTION_KEYS = tuple(key for key, _ in SECTIONS)
UNVERIFIED_PLACEHOLDER = "No statement in this section could be verified against the report data."

# Hard caps enforced after the LLM responds (and instructed in the prompt).
SECTION_LIMITS: Dict[str, Dict[str, int]] = {
    "executive_overview": {"paragraphs": 1, "bullets": 5},
    "sales_performance": {"paragraphs": 1, "bullets": 5},
    "product_performance": {"paragraphs": 1, "bullets": 6},
    "customer_performance": {"paragraphs": 1, "bullets": 6},
    "distributor_performance": {"paragraphs": 1, "bullets": 5},
    "negative_observations": {"paragraphs": 0, "bullets": 5},
    "positive_observations": {"paragraphs": 0, "bullets": 5},
    "recommendations": {"paragraphs": 0, "bullets": 5},
    "closing_summary": {"paragraphs": 1, "bullets": 4},
}

TOP_N = 8
BOTTOM_N = 5
MAX_OUTPUT_TOKENS = 3500
TARGET_WORD_MIN = 450
TARGET_WORD_MAX = 750

SYSTEM_PROMPT = """ROLE AND MANDATE
You are the Chief Commercial Analytics Officer preparing the Quarterly Executive Sales Brief for the senior leadership of APCOTEX Industries Limited. The primary audience is the Managing Director, Chief Financial Officer, Chief Marketing Officer and Chief Information Officer. Your output will appear, unchanged in meaning, in a formal board-facing PDF. Write with the discipline of a Fortune-500 management pack: precise, structured, decision-oriented and free of narrative padding.

You will receive a single JSON object named the analytics brief. Every sales figure, ranking, share, customer name, product name, distributor name, period label and comparison in that object has already been computed by the company's authoritative analytics engine under the user's selected filters and access scope. Your sole task is to interpret that brief into a compact, structured executive summary. You are not permitted to recalculate, extrapolate, invent or adorn the data.

PURPOSE OF THIS BRIEF
Leadership already sees the charts and KPI cards in Part A of the PDF. Part B must therefore be a short management interpretation — not a restatement of every chart, not an essay, and not a research paper. The complete PDF (charts plus this summary) must fit approximately five pages. Keep the entire summary between 450 and 750 words. Prefer bullets over paragraphs. Prefer one crisp sentence over three soft ones. Do not write transitional filler, rhetorical questions, motivational language or speculative storytelling.

ABSOLUTE DATA INTEGRITY RULES
1. Use only facts that appear in the analytics brief. Never invent sales quantities, percentages, rankings, customer names, product names, distributor names, months, quarters, trends or causal explanations.
2. Copy every numeric value exactly as printed in the brief, including decimal precision (for example "12.345 MT" or "18.2%"). Do not round, re-average, recompute shares, or derive any new total, difference, ratio or percentage that is not already present.
3. All quantities are sales quantity in metric tonnes (MT). The brief contains no monetary values. Never mention revenue, value, realisation, rupees, pricing or margin.
4. Growth or decline may be stated only when a comparison object has "available": true. If a comparison is unavailable, write clearly that the comparison could not be established. Never treat missing history as zero sales.
5. Entries labelled lowest or lowest_with_recorded_sales indicate low volume within the selected period. Low volume is not the same as declining performance. Call a name declining only when a comparison decrease is present.
6. The field no_sales_in_current_period lists names that had recorded sales in the comparison period and none in the selected period, within the same filters. State this carefully; do not invent reasons for the absence.
7. If monthly_quality.monthly_basis is anything other than "recorded_months", monthly values are partly or fully allocated from quarterly totals. Do not treat month-to-month movement as a genuine operational trend; note the data limitation in one short bullet if you comment on months at all.
8. Do not speculate about causes (pricing, seasonality, competition, logistics, quality, demand cycles or management actions). Observations must stay descriptive. Recommendations must stay action-oriented and clearly separated from observations.
9. The Negative Observations and Business Risks section is mandatory and must be candid. Do not produce an artificially positive brief. If the data does not support a particular risk, omit that risk; do not invent one.
10. Respect the filter scope. Comment only on the period and dimensions present in the brief. If a single distributor is selected, do not write as if the whole network is in view.

STRUCTURE AND BREVITY REQUIREMENTS
Return a JSON object only — no markdown fences, no preamble, no epilogue. The shape must be:
{"sections":[{"key":"<section_key>","paragraphs":["..."],"bullets":["..."]}]}

Include every section key below, exactly once, in this exact order:
executive_overview, sales_performance, product_performance, customer_performance, distributor_performance, negative_observations, positive_observations, recommendations, closing_summary.

Each section must be structured for rapid executive scanning:
- paragraphs: at most the limit below; each paragraph at most two sentences; no multi-paragraph essays.
- bullets: the primary content vehicle; each bullet one sentence, starting with the insight, then the supporting figure or name from the brief.
- Prefer bullets. Where a section allows zero paragraphs, put all content in bullets.
- Never exceed the bullet or paragraph limits. Prefer fewer high-signal bullets over many weak ones.
- Target total length for the whole summary: 450–750 words. Hard upper bound: 800 words.

SECTION CONTENT BRIEF

1. executive_overview
   - paragraphs: exactly 1 (two sentences maximum) stating the reporting period, total sales quantity, and overall business assessment grounded in the brief.
   - bullets: 3–5 covering customers served, products sold, active distributors, and the most material comparison result if available.

2. sales_performance
   - paragraphs: 0 or 1 short lead sentence.
   - bullets: 3–5 on period trend, monthly pattern (only if monthly_quality permits), concentration or distribution of sales, and the principal change versus an available comparison period.

3. product_performance
   - paragraphs: 0 or 1 short lead sentence.
   - bullets: 4–6 naming highest-selling products with quantities/shares; lowest-volume products with recorded sales (explicitly as low volume, not decline); any significant product increases or decreases from available comparisons; products with no sales in the current period where listed.

4. customer_performance
   - paragraphs: 0 or 1 short lead sentence.
   - bullets: 4–6 naming top customers with quantities/shares; notable concentration risk if the brief shows a high top-customer share; lowest-volume customers with recorded sales; material customer movers from available comparisons.

5. distributor_performance
   - paragraphs: 0 or 1 short lead sentence.
   - bullets: 3–5 on best and lowest contributing distributors, contribution concentration, and material distributor-level increases or declines where comparisons exist. If only one distributor is in scope, state that clearly in one bullet and keep the section short.

6. negative_observations
   - paragraphs: none.
   - bullets: 3–5 genuine, data-backed risks (declines, concentration, low performers that matter, missing comparison where leadership would expect one, unusual volatility supported by the brief). No invented risks.

7. positive_observations
   - paragraphs: none.
   - bullets: 3–5 measurable achievements (strong products, customers or distributors; positive trends supported by comparisons; improved contribution where shown).

8. recommendations
   - paragraphs: none.
   - bullets: 3–5 practical next-quarter actions that follow directly from the data (focus accounts, products needing attention, distributor follow-up, investigation items). Phrase each as an action. Do not restate the entire analysis.

9. closing_summary
   - paragraphs: exactly 1 sentence that closes the assessment of the quarter.
   - bullets: exactly 4, each beginning with one of these labels in this order:
     "What went well:"
     "What did not go well:"
     "Needs management attention:"
     "Priorities for next quarter:"

TONE AND STYLE
- Board-pack English: formal, calm, specific and economical.
- Lead with the point; support with the figure.
- Distinguish observations from recommendations at all times.
- Do not use adjectives such as "amazing", "catastrophic", "incredible" or similar hyperbole.
- Do not open sections with "In conclusion", "It is important to note", "Overall, it can be observed that" or similar filler.
- Do not repeat the same name or figure across sections unless it is essential to a different decision point.

QUALITY GATE BEFORE YOU ANSWER
Confirm silently that: every number you wrote appears in the brief; every named entity appears in the brief; no section exceeds its paragraph/bullet limits; the whole summary is between 450 and 750 words; negative_observations is candid; closing_summary has exactly four labelled bullets; the response is valid JSON matching the required shape. Then return only that JSON."""


class ExecutiveSummaryError(AppException):
    """The LLM could not produce a usable executive summary."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=502, error_code="EXECUTIVE_SUMMARY_FAILED")


def _slim(rows: List[Dict[str, Any]], *, keep: Iterable[str] = ("rank", "name", "mt_display", "share_pct", "records")):
    fields = tuple(keep)
    return [{k: r[k] for k in fields if k in r} for r in rows]


def _slim_movers(block: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in block.items():
        if isinstance(value, list):
            out[key] = [
                {
                    k: (f"{v:.3f}" if k.endswith("_mt") and isinstance(v, (int, float)) else v)
                    for k, v in item.items()
                }
                for item in value
            ]
        else:
            out[key] = value
    return out


def llm_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """The facts the LLM may use. Lists are trimmed; counts stay complete."""

    def ranking(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        positive = [r for r in rows if r["mt"] > 0]
        lowest = positive[-BOTTOM_N:][::-1] if len(positive) > TOP_N else []
        return {
            "count_with_records": len(rows),
            "highest": _slim(rows[:TOP_N]),
            "lowest_with_recorded_sales": _slim(lowest),
            "zero_or_negative_quantity": _slim([r for r in rows if r["mt"] <= 0][:BOTTOM_N]),
        }

    comparisons = []
    for comp in snapshot.get("comparisons") or []:
        item = {k: comp[k] for k in ("key", "description", "period", "available")}
        if comp.get("available"):
            for k in (
                "total_mt_display",
                "change_pct",
                "customers_with_sales",
                "products_with_sales",
                "distributors_with_sales",
            ):
                item[k] = comp.get(k)
            item["change_mt"] = f"{comp['change_mt']:.3f}"
            for dim in ("products", "customers", "distributors"):
                item[dim] = _slim_movers(comp[dim])
        comparisons.append(item)

    kpis = snapshot["kpis"]
    return {
        "reporting_period": snapshot["period"],
        "filters": snapshot["filter_lines"],
        "unit": snapshot["unit"],
        "output_contract": {
            "format": "structured_board_brief",
            "target_words": f"{TARGET_WORD_MIN}-{TARGET_WORD_MAX}",
            "max_words": 800,
            "section_limits": SECTION_LIMITS,
            "style": "bullet_first_no_essay",
        },
        "kpis": {
            "total_sales_quantity": f"{kpis['total_sales_mt_display']} MT",
            "customers_served": kpis["total_customers"],
            "products_sold": kpis["total_products"],
            "active_distributors": kpis["active_distributors"],
            "average_quarterly_sales": f"{kpis.get('average_quarterly_sales_mt_display') or '0.000'} MT",
            "sales_records": kpis["record_count"],
        },
        "quarterly_totals": [
            {
                "quarter": q.get("display_label") or q.get("label"),
                "mt": q.get("sales_mt_display"),
                "records": q.get("record_count"),
            }
            for q in snapshot["charts"].get("quarterly_totals") or []
        ],
        "monthly_sales": [{"month": m["month"], "mt": m["mt_display"]} for m in snapshot.get("monthly") or []],
        "monthly_quality": snapshot.get("monthly_quality"),
        "products": ranking(snapshot["products"]),
        "customers": ranking(snapshot["customers"]),
        "distributors": ranking(snapshot["distributors"]),
        "concentration": snapshot["concentration"],
        "comparisons": comparisons,
    }


# ---------------------------------------------------------------- parsing

def _extract_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ExecutiveSummaryError("The LLM response was not a report. Please try again.")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        raise ExecutiveSummaryError("The LLM response could not be read. Please try again.") from None
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise ExecutiveSummaryError("The LLM response did not contain report sections. Please try again.")
    return data


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(str(v).split()) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _truncate_sentence(text: str, max_words: int = 40) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    clipped = " ".join(words[:max_words]).rstrip(" ,;")
    return clipped + "."


# ---------------------------------------------------------------- verification

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d[\d,]*(?:\.\d+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
_FREE_INTEGERS = {str(n) for n in range(0, 11)}


def _variants(value: float) -> Set[str]:
    out = set()
    for v in (value, abs(value)):
        for decimals in range(4):
            out.add(f"{v:.{decimals}f}")
    return out


def allowed_numbers(payload: Any) -> Set[str]:
    """Every number that appears anywhere in the facts, at 0-3 decimals."""
    allowed: Set[str] = set(_FREE_INTEGERS)

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            allowed.update(_variants(float(node)))
        elif isinstance(node, str):
            for token in _NUMBER_RE.findall(node):
                number = token.replace(",", "")
                allowed.add(number)
                try:
                    allowed.update(_variants(float(number)))
                except ValueError:
                    pass
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return allowed


def unverified_numbers(text: str, allowed: Set[str]) -> List[str]:
    return [t for t in _NUMBER_RE.findall(text) if t.replace(",", "") not in allowed]


def _verify_paragraph(text: str, allowed: Set[str], removed: List[str]) -> str:
    kept = []
    for sentence in _SENTENCE_RE.split(text):
        if unverified_numbers(sentence, allowed):
            removed.append(sentence)
        else:
            kept.append(sentence)
    return " ".join(kept).strip()


def build_summary(text: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the LLM JSON, drop unverifiable figures, and enforce board-brief structure limits."""
    data = _extract_json(text)
    by_key: Dict[str, Dict[str, Any]] = {}
    for item in data["sections"]:
        if isinstance(item, dict) and item.get("key") in SECTION_KEYS and item["key"] not in by_key:
            by_key[item["key"]] = item
    missing = [k for k in SECTION_KEYS if k not in by_key]
    if len(missing) > 2:
        raise ExecutiveSummaryError("The LLM response was incomplete. Please try again.")

    allowed = allowed_numbers(payload)
    removed: List[str] = []
    sections = []
    words = 0
    for key, title in SECTIONS:
        limits = SECTION_LIMITS[key]
        item = by_key.get(key) or {}
        paragraphs = [
            _truncate_sentence(p)
            for p in (_verify_paragraph(p, allowed, removed) for p in _strings(item.get("paragraphs")))
            if p
        ][: limits["paragraphs"]]
        bullets = []
        for bullet in _strings(item.get("bullets")):
            if unverified_numbers(bullet, allowed):
                removed.append(bullet)
                continue
            bullets.append(_truncate_sentence(bullet, max_words=36))
            if len(bullets) >= limits["bullets"]:
                break
        if not paragraphs and not bullets:
            if limits["paragraphs"] > 0:
                paragraphs = [UNVERIFIED_PLACEHOLDER]
            else:
                bullets = [UNVERIFIED_PLACEHOLDER]
        words += sum(len(p.split()) for p in paragraphs + bullets)
        sections.append({"key": key, "title": title, "paragraphs": paragraphs, "bullets": bullets})
    if removed:
        logger.info("Executive summary verifier removed {} statement(s)", len(removed))
    if words > 900:
        logger.info("Executive summary still long after capping | words={}", words)
    return {
        "sections": sections,
        "word_count": words,
        "verification": {"removed_statements": len(removed), "removed": removed[:20]},
    }


def generate_summary(
    snapshot: Dict[str, Any],
    *,
    completer: Callable[..., Any],
    model_id: Optional[str],
) -> Tuple[Dict[str, Any], Any]:
    """One LLM call. Returns (verified summary, completion)."""
    payload = llm_payload(snapshot)
    try:
        completion = completer(
            system=SYSTEM_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
            model_id=model_id,
            timeout=None,
            reason="executive_summary",
            max_tokens=MAX_OUTPUT_TOKENS,
        )
    except AppException:
        raise
    except Exception as exc:  # noqa: BLE001
        from app.llm.provider_clients import LlmProviderError

        logger.warning("Executive summary LLM call failed: {}", type(exc).__name__)
        message = str(exc) if isinstance(exc, LlmProviderError) else ""
        raise ExecutiveSummaryError(
            f"The LLM service could not generate the summary. {message}".strip()
            + " Please retry."
        ) from None
    return build_summary(completion.text, payload), completion
