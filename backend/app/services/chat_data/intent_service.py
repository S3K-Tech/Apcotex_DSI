"""Question → ``QuerySpec``.

Bedrock Converse (when enabled) proposes a JSON spec that must validate against
the closed schema; otherwise, or on any failure, a deterministic rule parser is
used. Neither path decides permissions or touches SQL. Financial years are
never inferred from today's date: a period must be named in the question or
carried in the validated previous spec.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import ValidationError

from app.constants.business_segments import BUSINESS_SEGMENTS, SEGMENT_ALIASES
from app.core.logging import get_logger
from app.schemas.chat_data import (
    AwaitingField,
    PeriodRef,
    QueryDimension,
    QueryFilters,
    QueryIntent,
    QueryMetric,
    QuerySpec,
    SortDirection,
)
from app.services.product_standardization import normalize_product_key
from app.utils.distributor_name import normalize_distributor_name

logger = get_logger(__name__)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*[,']?\s*(20\d{2})\b",
    re.I,
)
_FY_PAIR_RE = re.compile(
    r"\b(?:f\.?\s?y\.?\s*'?)?(20\d{2}|\d{2})\s*[-–/]\s*'?(20\d{2}|\d{2})\b", re.I
)
_FY_SINGLE_RE = re.compile(r"\bf\.?\s?y\.?\s*'?(20\d{2}|\d{2})\b(?!\s*[-–/]\s*\d)", re.I)
_QUARTER_RE = re.compile(r"\b(?:q\s?([1-4])|quarter\s+([1-4]))\b", re.I)
_RELATIVE_RE = re.compile(
    r"\b(this|last|previous|current|next|past|prior)\s+(year|fy|financial year|quarter|month|qtr)\b"
    r"|\b(ytd|year to date|qtd|mtd|today|yesterday|recent(?:ly)?|latest)\b",
    re.I,
)
_ALL_PERIODS_RE = re.compile(
    r"\b(all\s+(?:time|periods|years|available periods|the data)|overall|ever|across all periods)\b",
    re.I,
)
_UNSUPPORTED = [
    (re.compile(r"\b(revenue|price|pricing|prices|profit|margin|rupees?|inr|rs\.?|turnover|invoice value|sales value|amount|cost|discount|payment|outstanding|credit)\b", re.I),
     "The data holds quantities only (MT). Prices, values and revenue are not available."),
    (re.compile(r"\b(forecast|predict|projection|project(?:ed)?|budget|target|estimate next)\b", re.I),
     "Forecasts and targets are not available; I can only report recorded quantities."),
    (re.compile(r"\b(delete|update|insert|drop|modify|change the data|upload)\b", re.I),
     "Chat with Data is read-only and cannot change data."),
    (re.compile(r"\b(email|phone|address|contact|password|salary|employee)\b", re.I),
     "Contact and personal details are not available through Chat with Data."),
]
_EXPLAIN_RE = re.compile(r"\b(why|explain|reason|what drove|what caused|contribut\w*|drivers?|drove)\b", re.I)
_STRONG_EXPLAIN_RE = re.compile(r"\b(why|explain|reason|what drove|what caused)\b", re.I)
_COMPARE_RE = re.compile(r"\b(compare|comparison|vs\.?|versus|against|change|grow(?:th)?|grew|declin\w*|differ\w*|increase\w*|decrease\w*|dropp?\w*)\b", re.I)
_TREND_RE = re.compile(r"\b(trend|month[- ]?wise|monthly|month by month|over time|by month|each month|per month)\b", re.I)
_RANK_DESC_RE = re.compile(r"\b(top|highest|largest|biggest|best|leading|most|major|key)\b", re.I)
_RANK_ASC_RE = re.compile(r"\b(bottom|lowest|least|smallest|weakest)\b", re.I)
_BREAKDOWN_RE = re.compile(r"\b(breakdown|break down|split|by|wise|across|distribution|share|each)\b", re.I)
_COUNT_RE = re.compile(r"\b(how many|number of|count of|count|no\. of)\b", re.I)
_AGG_RE = re.compile(r"\b(total|sum|how much|volume|quantity|sales|sold|offtake|mt|tonnes?|tons?|purchased|bought)\b", re.I)
_DIMENSIONS: List[Tuple[re.Pattern, QueryDimension]] = [
    (re.compile(r"\bcustomers?\b|\bbuyers?\b|\bclients?\b", re.I), QueryDimension.CUSTOMER),
    (re.compile(r"\bproducts?\b|\bgrades?\b|\bitems?\b", re.I), QueryDimension.PRODUCT),
    (re.compile(r"\bdistributors?\b|\bdealers?\b|\bchannel partners?\b|\bcompanies\b", re.I), QueryDimension.DISTRIBUTOR),
    (re.compile(r"\bsegments?\b|\bsegment[- ]wise\b", re.I), QueryDimension.SEGMENT),
    (re.compile(r"\bquarters?\b|\bquarter[- ]wise\b|\bquarterly\b", re.I), QueryDimension.QUARTER),
    (re.compile(r"\bmonths?\b|\bmonth[- ]wise\b", re.I), QueryDimension.MONTH),
]
_CLEAR_FILTER_RE = re.compile(r"\ball\s+(distributors|segments|products|customers)\b", re.I)
_LIMIT_RE = re.compile(r"\b(?:top|bottom|first|best|largest|highest|lowest)\s+(\d{1,3})\b|\b(\d{1,3})\s+(?:customers|products|distributors|segments|months|quarters)\b", re.I)
_WORD_NUMBERS = {"three": 3, "five": 5, "ten": 10, "twenty": 20, "fifteen": 15, "seven": 7}
_WORD_LIMIT_RE = re.compile(r"\btop\s+(" + "|".join(_WORD_NUMBERS) + r")\b", re.I)
# "customer for A.B Brothers" names a distributor, not a customer: the name may not start
# with a preposition, and only trailing punctuation ends it (initials like "A.B" stay whole).
_CUSTOMER_MENTION_RE = re.compile(
    r"\bcustomer\s+(?!(?:in|for|during|from|of|by|with|across|between|under|at|on|the)\b)"
    r"[\"“']?([A-Za-z0-9&.,()'\- ]{3,80}?)[\"”']?"
    r"(?=\s+(?:in|for|during|from|of|by|with|across|between)\b|[?.!]*\s*$)",
    re.I,
)
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,80})[\"”]")
_PREP_ENTITY_RE = re.compile(
    r"\b(?:of|for|from|by|at)\s+((?:[A-Z][\w&.\-]*)(?:\s+(?:[A-Z][\w&.\-]*|&))*)"
)
_NOT_ENTITY_WORDS = {"fy", "q1", "q2", "q3", "q4", "all", "the", "mt", "kg", "april", "may", "june",
                     "july", "august", "september", "october", "november", "december", "january",
                     "february", "march", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
                     "jan", "feb", "mar", "quarter", "financial", "year"}


@dataclass
class RuleHints:
    """Authorized names the rule parser may spot in free text (caller's scope only)."""

    distributor_names: Sequence[str] = ()
    product_names: Sequence[str] = ()


@dataclass
class Interpretation:
    spec: QuerySpec
    source: str
    usage: Optional[Dict[str, int]] = None
    model_id: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class _Periods:
    periods: List[PeriodRef]
    quarters_without_fy: List[int]
    ambiguous_fy: List[int]
    relative: bool
    all_periods: bool


def _fy_from_pair(a: str, b: str) -> Optional[int]:
    start = int(a) if len(a) == 4 else 2000 + int(a)
    end = int(b) if len(b) == 4 else (start // 100) * 100 + int(b)
    if end == start + 1:
        return start
    return None


def extract_periods(text: str) -> _Periods:
    tokens: List[Tuple[int, str, Any]] = []
    taken: List[Tuple[int, int]] = []
    for m in _MONTH_RE.finditer(text):
        month = _MONTHS[m.group(1).lower()]
        year = int(m.group(2))
        fy = year if month >= 4 else year - 1
        tokens.append((m.start(), "month", PeriodRef(fy_start=fy, month=month)))
        taken.append(m.span())
    for m in _FY_PAIR_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        fy = _fy_from_pair(m.group(1), m.group(2))
        if fy is not None and 2000 <= fy <= 2100:
            tokens.append((m.start(), "fy", fy))
            taken.append(m.span())
    ambiguous: List[int] = []
    for m in _FY_SINGLE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        raw = m.group(1)
        ambiguous.append(int(raw) if len(raw) == 4 else 2000 + int(raw))
    quarters = [(m.start(), int(m.group(1) or m.group(2))) for m in _QUARTER_RE.finditer(text)]

    fys = [(pos, fy) for pos, kind, fy in tokens if kind == "fy"]
    bound_fy_positions: Set[int] = set()
    ordered: List[Tuple[int, PeriodRef]] = []
    quarters_without_fy: List[int] = []
    for pos, q in quarters:
        after = [f for f in fys if f[0] > pos]
        before = [f for f in fys if f[0] < pos]
        target = after[0] if after else (before[-1] if before else None)
        if target is None:
            quarters_without_fy.append(q)
            continue
        bound_fy_positions.add(target[0])
        ordered.append((pos, PeriodRef(fy_start=target[1], quarter=q)))
    for pos, fy in fys:
        if pos not in bound_fy_positions:
            ordered.append((pos, PeriodRef(fy_start=fy)))
    for pos, kind, value in tokens:
        if kind == "month":
            ordered.append((pos, value))
    ordered.sort(key=lambda t: t[0])
    periods: List[PeriodRef] = []
    for _, p in ordered:
        if p not in periods:
            periods.append(p)
    return _Periods(
        periods=periods,
        quarters_without_fy=quarters_without_fy,
        ambiguous_fy=ambiguous,
        relative=bool(_RELATIVE_RE.search(text)),
        all_periods=bool(_ALL_PERIODS_RE.search(text)),
    )


def years_mentioned(text: str) -> Set[int]:
    years: Set[int] = {int(y) for y in re.findall(r"\b(20\d{2})\b", text)}
    for m in _FY_PAIR_RE.finditer(text):
        fy = _fy_from_pair(m.group(1), m.group(2))
        if fy:
            years.update({fy, fy + 1})
    for m in _FY_SINGLE_RE.finditer(text):
        raw = m.group(1)
        years.add(int(raw) if len(raw) == 4 else 2000 + int(raw))
    return years


def _dimension_after(text: str, anchor: Optional[int]) -> Optional[QueryDimension]:
    found: List[Tuple[int, QueryDimension]] = []
    for pattern, dim in _DIMENSIONS:
        for m in pattern.finditer(text):
            found.append((m.start(), dim))
    if not found:
        return None
    found.sort(key=lambda t: t[0])
    if anchor is not None:
        after = [d for pos, d in found if pos >= anchor]
        if after:
            return after[0]
    return found[0][1]


def _segment_mention(text: str) -> Optional[str]:
    lowered = text.lower()
    best: Optional[str] = None
    for alias in sorted(set(SEGMENT_ALIASES) | {s.lower() for s in BUSINESS_SEGMENTS}, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", lowered):
            best = alias
            break
    return best


def _name_mention(text: str, names: Sequence[str], normalizer: Callable[[str], str], min_len: int) -> Optional[str]:
    haystack = normalizer(text)
    best: Optional[str] = None
    best_len = 0
    for name in names:
        key = normalizer(name)
        if len(key) < min_len:
            continue
        if key in haystack and len(key) > best_len:
            best, best_len = name, len(key)
    return best


def _name_tokens(text: str) -> List[Tuple[str, int, int]]:
    return [
        ("AND" if m.group(0) == "&" else m.group(0).upper(), m.start(), m.end())
        for m in re.finditer(r"[A-Za-z0-9]+|&", text)
    ]


def _distributor_prefix_mention(text: str, names: Sequence[str]) -> Optional[str]:
    """The question's words that spell the leading words of an authorized distributor name.

    "Goel minerals" → "GOEL MINERALS & CHEMICALS". At least two words and five letters
    must match. The user's own words are returned so the resolver can still ask when
    several distributors share them.
    """
    words = _name_tokens(text)
    best: Optional[Tuple[int, int]] = None
    for name in names:
        name_words = [w for w, _, _ in _name_tokens(name)]
        if len(name_words) < 2:
            continue
        for i in range(len(words)):
            n = 0
            while n < len(name_words) and i + n < len(words) and words[i + n][0] == name_words[n]:
                n += 1
            if n < 2 or sum(len(w) for w in name_words[:n]) < 5:
                continue
            if best is None or n > best[1] - best[0]:
                best = (i, i + n)
    if best is None:
        return None
    return text[words[best[0]][1] : words[best[1] - 1][2]]


_LEADING_ENTITY_RE = re.compile(
    r"^\s*(?P<lead>.+?)\s+(?:top|bottom|highest|lowest|largest|biggest|best|leading)\b", re.I
)
_LEADING_FILLER = {
    "show", "me", "what", "whats", "what's", "are", "is", "were", "was", "who", "which", "list",
    "give", "tell", "please", "pls", "plz", "our", "my", "get", "find", "display", "i", "want",
    "need", "to", "see", "know", "can", "you", "could", "would", "a", "an", "of", "for", "in",
    "and", "hi", "hello", "sales", "quantity", "total", "volume", "fetch", "provide", "current",
    "overall", "us", "let", "do", "we", "have", "with", "by", "from", "at", "on", "about",
    "customer", "customers", "product", "products", "distributor", "distributors", "segment",
    "segments", "buyers", "clients", "rank", "ranking", "ranked", "monthly", "quarterly", "yearly",
    "annual", "this", "last", "year", "month", "full", "only", "just", "now", "then", "also",
}


def _leading_entity(text: str, taken: Set[Optional[str]], product_words: Set[str]) -> Optional[str]:
    """An unrecognised name before the ranking word ("Xyz Traders top 5 customers").

    Kept as a distributor mention so it is resolved (or reported as not found)
    instead of being dropped, which would silently rank across every distributor.
    """
    m = _LEADING_ENTITY_RE.match(text)
    if not m:
        return None
    lead = m.group("lead")
    for name in taken:
        if name:
            lead = re.sub(re.escape(name), " ", lead, flags=re.I)
    segment_words = set(SEGMENT_ALIASES) | {s.lower() for s in BUSINESS_SEGMENTS}
    kept: List[str] = []
    for word in re.findall(r"[A-Za-z][\w&.'\-]*", lead):
        folded = word.lower().strip(".'")
        if folded in _LEADING_FILLER or folded in _NOT_ENTITY_WORDS or re.fullmatch(r"(fy|q)\d*", folded):
            continue
        if folded in segment_words or folded.upper() in product_words:
            return None
        kept.append(word)
    name = " ".join(kept).strip(" .,&")
    return name if len(name) >= 3 else None


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_distributor_name(value))


def _product_compact(value: str) -> str:
    return " " + re.sub(r"[^A-Z0-9]+", " ", normalize_product_key(value)).strip() + " "


def _limit(text: str) -> Optional[int]:
    m = _LIMIT_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    w = _WORD_LIMIT_RE.search(text)
    if w:
        return _WORD_NUMBERS[w.group(1).lower()]
    return None


def _quarters_in_previous_fy(per: _Periods, previous: QuerySpec) -> List[PeriodRef]:
    """A bare quarter after a one-year answer means that quarter of the same FY ("Q2" → Q2 FY 2026-27)."""
    years = {p.fy_start for p in previous.periods}
    if not per.quarters_without_fy or per.periods or len(years) != 1:
        return []
    fy = next(iter(years))
    return [PeriodRef(fy_start=fy, quarter=q) for q in per.quarters_without_fy[:2]]


class RuleParser:
    """Deterministic interpretation used when Bedrock is off or fails."""

    def __init__(self, hints: Optional[RuleHints] = None) -> None:
        self.hints = hints or RuleHints()

    def filters(self, text: str) -> QueryFilters:
        distributor = _name_mention(text, self.hints.distributor_names, _compact, 4)
        product = None
        haystack = _product_compact(text)
        best = 0
        for name in self.hints.product_names:
            key = _product_compact(name)
            if len(key.strip()) >= 3 and key in haystack and len(key) > best:
                product, best = name, len(key)
        customer = None
        m = _CUSTOMER_MENTION_RE.search(text)
        if m and not re.match(r"^(s|count|wise|base|list)\b", m.group(1), re.I):
            customer = m.group(1).strip(" ,.")
        if not customer:
            q = _QUOTED_RE.search(text)
            if q and q.group(1).strip() not in {distributor, product}:
                customer = q.group(1).strip()
        if distributor is None:
            without_customer = re.sub(re.escape(customer), " ", text, flags=re.I) if customer else text
            distributor = _distributor_prefix_mention(without_customer, self.hints.distributor_names)
        # Segment words inside an entity name ("Secret Glove Co", "NBR LATEX") are not segments.
        segment_text = _QUOTED_RE.sub(" ", text)
        for name in (customer, distributor, product):
            if name:
                segment_text = re.sub(re.escape(name), " ", segment_text, flags=re.I)
        segment = _segment_mention(segment_text)
        if distributor is None:
            distributor = self._unknown_named_entity(text, {segment, product, customer})
        if distributor is None and not customer:
            product_words = {w for p in self.hints.product_names for w, _, _ in _name_tokens(p)}
            distributor = _leading_entity(text, {segment, product, customer}, product_words)
        return QueryFilters(segment=segment, distributor=distributor, product=product, customer=customer)

    @staticmethod
    def _unknown_named_entity(text: str, taken: Set[Optional[str]]) -> Optional[str]:
        """A capitalised name after of/for/from/by that matched nothing authorized.

        Kept as a distributor mention so it is resolved (and rejected) within scope
        instead of being silently dropped.
        """
        segment_words = set(SEGMENT_ALIASES) | {s.lower() for s in BUSINESS_SEGMENTS}
        for m in _PREP_ENTITY_RE.finditer(text):
            words = m.group(1).split()
            while words and words[0].lower() in _NOT_ENTITY_WORDS:
                words = words[1:]
            cut = next((i for i, w in enumerate(words) if w.lower().strip(".") in _NOT_ENTITY_WORDS), len(words))
            words = words[:cut]
            name = " ".join(words).strip(" .,&")
            if len(name) < 3 or name.lower() in segment_words:
                continue
            if any(t and name.casefold() == t.casefold() for t in taken):
                continue
            if _QUARTER_RE.fullmatch(name) or re.fullmatch(r"[\d\-–/ ]+", name):
                continue
            return name
        return None

    def parse(self, question: str, previous: Optional[QuerySpec] = None) -> QuerySpec:
        text = question.strip()
        if previous is not None and previous.awaiting is not None:
            merged = self._answer_awaiting(text, previous)
            if merged is not None:
                return merged

        for pattern, reason in _UNSUPPORTED:
            if pattern.search(text):
                return QuerySpec(intent=QueryIntent.UNSUPPORTED, unsupported_reason=reason)

        per = extract_periods(text)
        found_filters = self.filters(text)
        limit = _limit(text)

        intent: Optional[QueryIntent] = None
        direction = SortDirection.DESC
        anchor: Optional[int] = None
        metric = QueryMetric.QUANTITY_MT
        dimension: Optional[QueryDimension] = None

        if _EXPLAIN_RE.search(text) and (len(per.periods) >= 2 or _STRONG_EXPLAIN_RE.search(text)):
            intent = QueryIntent.EXPLAIN_CHANGE
        elif _COMPARE_RE.search(text) and (len(per.periods) >= 2 or re.search(r"\b(compare|vs\.?|versus)\b", text, re.I)):
            intent = QueryIntent.COMPARE
        elif _TREND_RE.search(text):
            intent = QueryIntent.TREND
            dimension = QueryDimension.MONTH
        elif (m := _RANK_ASC_RE.search(text)) is not None:
            intent, direction, anchor = QueryIntent.RANK, SortDirection.ASC, m.start()
        elif (m := _RANK_DESC_RE.search(text)) is not None and not _COUNT_RE.search(text):
            intent, anchor = QueryIntent.RANK, m.start()
        elif _COUNT_RE.search(text):
            intent = QueryIntent.AGGREGATE
            dim = _dimension_after(text, _COUNT_RE.search(text).start())
            if dim == QueryDimension.CUSTOMER:
                metric = QueryMetric.CUSTOMER_COUNT
            elif dim == QueryDimension.PRODUCT:
                metric = QueryMetric.PRODUCT_COUNT
            else:
                return QuerySpec(
                    intent=QueryIntent.UNSUPPORTED,
                    unsupported_reason="I can count customers or products. Other counts are not supported yet.",
                )
        elif _BREAKDOWN_RE.search(text) and _dimension_after(text, None) is not None:
            intent = QueryIntent.BREAKDOWN
        elif _AGG_RE.search(text):
            intent = QueryIntent.AGGREGATE

        if intent in (QueryIntent.RANK, QueryIntent.BREAKDOWN):
            dimension = _dimension_after(text, anchor)
            if intent == QueryIntent.BREAKDOWN and dimension is None:
                intent = QueryIntent.AGGREGATE

        if intent is None and previous is not None:
            return self._follow_up(text, previous, per, found_filters, limit)

        has_signal = any(
            [per.periods, per.quarters_without_fy, found_filters.segment, found_filters.distributor,
             found_filters.product, found_filters.customer]
        )
        if intent is None:
            if has_signal:
                intent = QueryIntent.AGGREGATE
            else:
                return QuerySpec(
                    intent=QueryIntent.UNSUPPORTED,
                    unsupported_reason=(
                        "I can answer questions about recorded sales quantities: totals, top customers, "
                        "products or distributors, breakdowns, comparisons and monthly trends."
                    ),
                )

        spec = QuerySpec(
            intent=intent,
            metric=metric,
            dimension=dimension,
            filters=found_filters,
            periods=per.periods[:2],
            all_periods=per.all_periods,
            direction=direction,
            limit=limit or 10,
        )
        if previous is not None:
            spec = self._inherit(spec, previous, text, per, found_filters)
        return self._period_guard(spec, per)

    # ---- follow-ups -----------------------------------------------------------------

    def _inherit(self, spec: QuerySpec, previous: QuerySpec, text: str, per: _Periods, found: QueryFilters) -> QuerySpec:
        data = spec.model_dump()
        prev_filters = previous.filters.model_dump()
        for key, value in prev_filters.items():
            if data["filters"].get(key) is None and value:
                data["filters"][key] = value
        for m in _CLEAR_FILTER_RE.finditer(text):
            data["filters"][m.group(1).lower().rstrip("s")] = None
        bound = _quarters_in_previous_fy(per, previous)
        if not spec.periods and not spec.all_periods and not per.quarters_without_fy:
            data["periods"] = [p.model_dump() for p in previous.periods]
            data["all_periods"] = previous.all_periods
        elif not spec.periods and bound:
            data["periods"] = [p.model_dump() for p in bound]
            data["all_periods"] = False
        return QuerySpec.model_validate(data)

    def _follow_up(self, text: str, previous: QuerySpec, per: _Periods, found: QueryFilters, limit: Optional[int]) -> QuerySpec:
        data = previous.model_dump()
        data["awaiting"] = None
        data["clarification_question"] = None
        for key, value in found.model_dump().items():
            if value:
                data["filters"][key] = value
        for m in _CLEAR_FILTER_RE.finditer(text):
            data["filters"][m.group(1).lower().rstrip("s")] = None
        bound = _quarters_in_previous_fy(per, previous)
        if per.periods:
            data["periods"] = [p.model_dump() for p in per.periods[:2]]
            data["all_periods"] = False
        elif bound:
            data["periods"] = [p.model_dump() for p in bound]
            data["all_periods"] = False
        elif per.all_periods:
            data["periods"] = []
            data["all_periods"] = True
        if limit:
            data["limit"] = limit
        dim = _dimension_after(text, None)
        if dim is not None and previous.intent in (QueryIntent.RANK, QueryIntent.BREAKDOWN):
            data["dimension"] = dim
        elif dim is not None and previous.intent == QueryIntent.AGGREGATE:
            data["intent"] = QueryIntent.BREAKDOWN
            data["dimension"] = dim
        spec = QuerySpec.model_validate(data)
        return self._period_guard(spec, per)

    def _answer_awaiting(self, text: str, previous: QuerySpec) -> Optional[QuerySpec]:
        data = previous.model_dump()
        awaiting = previous.awaiting
        data["awaiting"] = None
        data["clarification_question"] = None
        if awaiting in (AwaitingField.PERIOD, AwaitingField.SECOND_PERIOD):
            per = extract_periods(text)
            if per.all_periods and awaiting == AwaitingField.PERIOD:
                data["all_periods"] = True
                data["pending_quarters"] = []
                return QuerySpec.model_validate(data)
            new_periods = list(per.periods)
            if previous.pending_quarters and new_periods and all(
                p.quarter is None and p.month is None for p in new_periods
            ):
                fy = new_periods[0].fy_start
                new_periods = [PeriodRef(fy_start=fy, quarter=q) for q in previous.pending_quarters]
            if not new_periods:
                return None
            if awaiting == AwaitingField.SECOND_PERIOD:
                combined = list(previous.periods) + [p for p in new_periods if p not in previous.periods]
                data["periods"] = [p.model_dump() for p in combined[:2]]
            else:
                data["periods"] = [p.model_dump() for p in new_periods[:2]]
            data["pending_quarters"] = []
            data["all_periods"] = False
            return QuerySpec.model_validate(data)
        if awaiting == AwaitingField.DIMENSION:
            dim = _dimension_after(text, None)
            if dim is None:
                return None
            data["dimension"] = dim
            return QuerySpec.model_validate(data)
        field_name = awaiting.value if awaiting else None
        if field_name in {"distributor", "customer", "product", "segment"}:
            choice = " ".join(text.split())[:200]
            looks_like_question = bool(
                extract_periods(choice).periods
                or _RANK_DESC_RE.search(choice)
                or _AGG_RE.search(choice)
                or _TREND_RE.search(choice)
                or choice.endswith("?")
            )
            if not choice or looks_like_question:
                return None
            data["filters"][field_name] = choice
            return QuerySpec.model_validate(data)
        return None

    @staticmethod
    def _period_guard(spec: QuerySpec, per: _Periods) -> QuerySpec:
        """Turn missing / relative / ambiguous periods into an explicit clarification."""
        if spec.intent in (QueryIntent.UNSUPPORTED, QueryIntent.CLARIFY):
            return spec
        if spec.periods or spec.all_periods:
            return spec
        data = spec.model_dump()
        if per.quarters_without_fy:
            data["pending_quarters"] = per.quarters_without_fy[:2]
            qs = " and ".join(f"Q{q}" for q in per.quarters_without_fy[:2])
            data["clarification_question"] = f"Which financial year do you mean for {qs}?"
        elif per.ambiguous_fy:
            y = per.ambiguous_fy[0]
            data["clarification_question"] = (
                f"Do you mean FY {y - 1}-{y % 100:02d} or FY {y}-{(y + 1) % 100:02d}? "
                "Please name the financial year in full."
            )
        elif per.relative:
            data["clarification_question"] = (
                "Please name the financial year explicitly (for example FY 2026-27). "
                "I don't assume the current date."
            )
        else:
            return spec
        data["awaiting"] = AwaitingField.PERIOD
        return QuerySpec.model_validate(data)


# ---- Bedrock interpretation ----------------------------------------------------------

_SYSTEM_PROMPT = """You convert a business question about distributor sales quantities into JSON.
Return ONLY one JSON object, no prose, matching this schema:
{
 "intent": "aggregate"|"rank"|"breakdown"|"compare"|"trend"|"explain_change"|"clarify"|"unsupported",
 "metric": "quantity_mt"|"customer_count"|"product_count",
 "dimension": null|"customer"|"product"|"distributor"|"segment"|"quarter"|"month",
 "filters": {"segment": str|null, "distributor": str|null, "customer": str|null, "product": str|null},
 "periods": [{"fy_start": int, "quarter": int|null, "month": int|null}],
 "all_periods": bool,
 "direction": "desc"|"asc",
 "limit": int,
 "clarification_question": str|null,
 "unsupported_reason": str|null
}
Rules:
- Indian financial year April-March. fy_start 2026 means FY 2026-27. Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar.
- Only use years written in the question or in previous_spec. Never assume the current year. If a quarter or month has no year and previous_spec has none, use intent "clarify".
- "rank" = top/bottom N by quantity (needs dimension). "breakdown" = quantity split by a dimension. "compare" = two periods. "explain_change" = which customers/products contributed to a change between two periods. "trend" = monthly quantities in a financial year (dimension "month").
- At most 2 periods. limit between 1 and 50, default 10.
- Copy entity names exactly as the user wrote them into filters. Do not invent names.
- Data has quantities only (MT). Revenue, price, value, forecasts, personal data, or data changes => "unsupported" with a short reason.
- For follow-up questions, start from previous_spec and change only what the user asked to change.
- Ambiguous or incomplete questions => "clarify" with one short question."""


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).rstrip("`").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Model output is not an object")
    return data


class IntentService:
    def __init__(
        self,
        *,
        use_llm: bool,
        completer: Optional[Callable[..., Any]] = None,
        model_id: Optional[str] = None,
        timeout: Optional[float] = None,
        hints: Optional[Callable[[], RuleHints]] = None,
        knowledge: Sequence[str] = (),
    ) -> None:
        self.use_llm = use_llm and completer is not None
        self.completer = completer
        self.model_id = model_id
        self.timeout = timeout
        self._hints = hints
        self._rules: Optional[RuleParser] = None
        self.knowledge = list(knowledge)

    @property
    def rules(self) -> RuleParser:
        if self._rules is None:
            self._rules = RuleParser(self._hints() if self._hints else None)
        return self._rules

    def interpret(self, question: str, previous: Optional[QuerySpec]) -> Interpretation:
        if previous is not None and previous.awaiting is not None:
            merged = self.rules._answer_awaiting(question.strip(), previous)
            if merged is not None:
                return Interpretation(spec=merged, source="rules")
            previous = previous.model_copy(update={"awaiting": None, "pending_quarters": []})
        if self.use_llm:
            try:
                return self._llm(question, previous)
            except Exception as exc:  # noqa: BLE001 — any model/validation failure falls back
                logger.warning("Chat intent LLM fallback: {}", type(exc).__name__)
        return Interpretation(spec=self.rules.parse(question, previous), source="rules")

    def _llm(self, question: str, previous: Optional[QuerySpec]) -> Interpretation:
        user_payload: Dict[str, Any] = {"question": question}
        if previous is not None:
            user_payload["previous_spec"] = previous.model_dump(
                mode="json", exclude={"awaiting", "pending_quarters", "clarification_question", "unsupported_reason"}
            )
        if self.knowledge:
            user_payload["glossary"] = self.knowledge[:4]
        completion = self.completer(
            system=_SYSTEM_PROMPT,
            user=json.dumps(user_payload, ensure_ascii=False),
            model_id=self.model_id,
            timeout=self.timeout,
            reason="chat_with_data_intent",
        )
        data = _extract_json(completion.text)
        data.pop("awaiting", None)
        data.pop("pending_quarters", None)
        try:
            spec = QuerySpec.model_validate(data)
        except ValidationError as exc:
            raise ValueError("Model JSON failed QuerySpec validation") from exc
        spec = self._ground_periods(spec, question, previous)
        return Interpretation(
            spec=spec,
            source="llm",
            usage=completion.usage(),
            model_id=getattr(completion, "model_id", None),
        )

    def _ground_periods(self, spec: QuerySpec, question: str, previous: Optional[QuerySpec]) -> QuerySpec:
        """Reject model-supplied periods whose year is not in the question or previous spec."""
        if not spec.periods:
            per = extract_periods(question)
            return RuleParser._period_guard(spec, per)
        allowed = years_mentioned(question)
        if previous is not None:
            for p in previous.periods:
                allowed.update({p.fy_start, p.fy_start + 1})
        grounded = all(p.fy_start in allowed or (p.fy_start + 1) in allowed for p in spec.periods)
        if grounded:
            return spec
        data = spec.model_dump()
        data["periods"] = []
        data["awaiting"] = AwaitingField.PERIOD
        data["clarification_question"] = "Which financial year do you mean? Please name it, for example FY 2026-27."
        quarters = [p.quarter for p in spec.periods if p.quarter]
        data["pending_quarters"] = quarters[:2]
        return QuerySpec.model_validate(data)
