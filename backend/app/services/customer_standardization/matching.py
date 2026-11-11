"""
Deterministic customer matching against approved mappings and established names.

Priority (first hit wins):
1. approved distributor-specific mapping
2. approved global mapping
3. exact legal-form-aware key match with the customer master, an approved
   standardized name, or a customer name already present in sales data
4. conservative similarity suggestion (NEEDS_REVIEW, never applied automatically)
5. unmapped

Similarity is only strong when every distinctive word (not a generic business
word such as RUBBER or INDUSTRIES and not a legal word) of both names has a
close counterpart, legal forms agree and unit numbers agree. Nothing here
writes to the database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer_alias_mapping import CustomerAliasMapping
from app.models.customer_master import CustomerMaster
from app.models.report import Report
from app.models.sales_record import SalesRecord
from app.services.customer_standardization.normalize import (
    clean_display,
    customer_key,
    digit_groups,
    distinctive_tokens,
    key_tokens,
    legal_form,
)

AUTO_MATCHED = "AUTO_MATCHED"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNMAPPED = "UNMAPPED"

METHOD_DISTRIBUTOR_MAPPING = "DISTRIBUTOR_MAPPING"
METHOD_GLOBAL_MAPPING = "GLOBAL_MAPPING"
METHOD_MASTER_EXACT = "MASTER_EXACT"
METHOD_EXISTING_EXACT = "EXISTING_EXACT"
METHOD_EXACT_AMBIGUOUS = "EXACT_AMBIGUOUS"
METHOD_SIMILARITY_HIGH = "SIMILARITY_HIGH"
METHOD_SIMILARITY_MEDIUM = "SIMILARITY_MEDIUM"
METHOD_SIMILARITY_AMBIGUOUS = "SIMILARITY_AMBIGUOUS"
METHOD_MAPPING_CONFLICT = "MAPPING_CONFLICT"
METHOD_NONE = "NONE"

SOURCE_MASTER = "master"
SOURCE_APPROVED = "approved"
SOURCE_SALES = "sales"
_SOURCE_RANK = {SOURCE_MASTER: 0, SOURCE_APPROVED: 1, SOURCE_SALES: 2}

HIGH_CONFIDENCE = 90.0
MEDIUM_CONFIDENCE = 80.0
MIN_ALTERNATIVE = 50.0
_GENERIC_ONLY_CAP = 50.0
_DISTINCTIVE_MISMATCH_CAP = 60.0
_DIGIT_MISMATCH_CAP = 60.0
_LEGAL_MISMATCH_CAP = 70.0
_LEGAL_MISSING_CAP = 89.0
_AMBIGUITY_GAP = 2.0
_MAX_ALTERNATIVES = 5
_MAX_CANDIDATES = 300


@dataclass
class CustomerMatch:
    raw: str
    key: str
    status: str
    method: str
    suggested: Optional[str] = None
    suggested_source: Optional[str] = None
    confidence: float = 0.0
    mapping_id: Optional[int] = None
    mapping_scope: Optional[str] = None
    alternatives: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.method in (METHOD_DISTRIBUTOR_MAPPING, METHOD_GLOBAL_MAPPING)

    @property
    def confidence_tier(self) -> str:
        if self.status == AUTO_MATCHED:
            return "EXACT"
        if self.confidence >= HIGH_CONFIDENCE:
            return "HIGH"
        if self.confidence >= MEDIUM_CONFIDENCE:
            return "MEDIUM"
        return "LOW"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_customer": self.raw,
            "normalized_key": self.key,
            "status": self.status,
            "method": self.method,
            "suggested_customer": self.suggested,
            "suggested_source": self.suggested_source,
            "confidence": round(self.confidence, 1),
            "confidence_tier": self.confidence_tier,
            "mapping_id": self.mapping_id,
            "mapping_scope": self.mapping_scope,
            "approved": self.approved,
            "alternatives": self.alternatives,
        }


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return fuzz.ratio(a, b) >= 80


def _covered(tokens: Sequence[str], other: Sequence[str]) -> bool:
    return all(any(_close(t, o) for o in other) for t in tokens)


def similarity(query: object, candidate: object) -> float:
    """0-100 similarity between two customer names, strict about identity words."""
    qt = key_tokens(query) if not isinstance(query, list) else query
    ct = key_tokens(candidate) if not isinstance(candidate, list) else candidate
    if not qt or not ct:
        return 0.0
    q, c = " ".join(qt), " ".join(ct)
    score = max(float(fuzz.token_sort_ratio(q, c)), float(fuzz.ratio(q.replace(" ", ""), c.replace(" ", ""))))

    qd, cd = distinctive_tokens(qt), distinctive_tokens(ct)
    if not qd or not cd:
        # Only generic words like "RUBBER INDUSTRIES": never a strong match.
        score = min(score, _GENERIC_ONLY_CAP)
    elif not (_covered(qd, ct) and _covered(cd, qt)):
        if fuzz.ratio("".join(qd), "".join(cd)) < 90:
            score = min(score, _DISTINCTIVE_MISMATCH_CAP)

    ql, cl = set(legal_form(qt)), set(legal_form(ct))
    if ql and cl and ql != cl:
        # PRIVATE LIMITED vs LIMITED vs LLP are different legal entities.
        score = min(score, _LEGAL_MISMATCH_CAP)
    elif bool(ql) != bool(cl):
        # "KUMAR RUBBER" vs "KUMAR RUBBER PVT LTD": compare the names without the
        # legal words, but keep it a review-only suggestion.
        qb = " ".join(t for t in qt if t not in ql)
        cb = " ".join(t for t in ct if t not in cl)
        if score > _DISTINCTIVE_MISMATCH_CAP and qb and cb:
            score = max(score, float(fuzz.token_sort_ratio(qb, cb)))
        score = min(score, _LEGAL_MISSING_CAP)

    if set(digit_groups(qt)) != set(digit_groups(ct)):
        score = min(score, _DIGIT_MISMATCH_CAP)
    return round(min(score, 99.0), 1)


def _blocks(tokens: Sequence[str]) -> Set[Tuple[str, str]]:
    distinct = distinctive_tokens(list(tokens))
    out: Set[Tuple[str, str]] = set()
    for tok in distinct:
        if len(tok) >= 3:
            out.add(("p", tok[:3]))
            out.add(("s", tok[-3:]))
    joined = "".join(distinct)
    if joined:
        out.add(("j", joined[:3]))
    return out


class _Reference:
    __slots__ = ("key", "tokens", "names")

    def __init__(self, key: str, tokens: List[str]) -> None:
        self.key = key
        self.tokens = tokens
        # display -> (source rank, row count)
        self.names: Dict[str, Tuple[int, int]] = {}

    def add(self, display: str, source: str, count: int) -> None:
        rank = _SOURCE_RANK[source]
        for existing in list(self.names):
            if existing.casefold() == display.casefold():
                old_rank, old_count = self.names[existing]
                if (rank, -count) < (old_rank, -old_count):
                    del self.names[existing]
                    self.names[display] = (rank, max(count, old_count))
                else:
                    self.names[existing] = (old_rank, max(count, old_count))
                return
        self.names[display] = (rank, count)

    def ranked(self) -> List[Tuple[str, int, int]]:
        return sorted(
            ((d, r, c) for d, (r, c) in self.names.items()),
            key=lambda item: (item[1], -item[2], item[0].casefold()),
        )

    def best(self) -> Tuple[str, str]:
        display, rank, _count = self.ranked()[0]
        return display, _source_name(rank)


def _source_name(rank: int) -> str:
    for name, value in _SOURCE_RANK.items():
        if value == rank:
            return name
    return SOURCE_SALES


class CustomerMatcher:
    """In-memory snapshot of approved mappings + established customer names."""

    def __init__(
        self,
        *,
        mappings: Iterable[CustomerAliasMapping],
        master_names: Iterable[str] = (),
        sales_names: Iterable[Tuple[str, int]] = (),
    ) -> None:
        self._global: Dict[str, List[CustomerAliasMapping]] = defaultdict(list)
        self._dist: Dict[Tuple[int, str], List[CustomerAliasMapping]] = defaultdict(list)
        self._refs: Dict[str, _Reference] = {}
        self._blocks: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        mapping_list = list(mappings)
        for m in mapping_list:
            key = m.normalized_original_name
            if not key:
                continue
            if m.distributor_id is None:
                self._global[key].append(m)
            else:
                self._dist[(int(m.distributor_id), key)].append(m)

        for name in master_names:
            self._add_reference(name, SOURCE_MASTER, 0)
        for m in mapping_list:
            self._add_reference(m.standardized_customer_name, SOURCE_APPROVED, 0)
        for name, count in sales_names:
            self._add_reference(name, SOURCE_SALES, int(count or 0))

    def _add_reference(self, name: object, source: str, count: int) -> None:
        display = clean_display(name)
        tokens = key_tokens(display)
        key = "".join(tokens)
        if not key:
            return
        ref = self._refs.get(key)
        if ref is None:
            ref = self._refs[key] = _Reference(key, tokens)
            for block in _blocks(tokens):
                self._blocks[block].add(key)
        ref.add(display, source, count)

    @classmethod
    def load(cls, db: Session, *, segments: Optional[List[str]] = None) -> "CustomerMatcher":
        """``segments`` None = all data; a list limits sales/master names to those segments."""
        mappings = list(
            db.scalars(
                select(CustomerAliasMapping).where(
                    CustomerAliasMapping.is_active.is_(True),
                    CustomerAliasMapping.status == "APPROVED",
                )
            )
        )
        master_stmt = select(CustomerMaster.customer_name).where(
            CustomerMaster.is_active.is_(True), CustomerMaster.is_deleted.is_(False)
        )
        sales_stmt = (
            select(SalesRecord.customer_name, func.count())
            .join(Report, Report.id == SalesRecord.report_id)
            .where(SalesRecord.is_deleted.is_(False), Report.is_deleted.is_(False))
            .group_by(SalesRecord.customer_name)
        )
        if segments is not None:
            lowered = [s.strip().lower() for s in segments if s and s.strip()]
            master_stmt = master_stmt.where(
                (CustomerMaster.segment.is_(None)) | (func.lower(func.trim(CustomerMaster.segment)).in_(lowered))
            )
            sales_stmt = sales_stmt.where(func.lower(func.trim(SalesRecord.segment)).in_(lowered))
        return cls(
            mappings=mappings,
            master_names=list(db.scalars(master_stmt)),
            sales_names=[(name, count) for name, count in db.execute(sales_stmt).all()],
        )

    # ------------------------------------------------------------ lookups
    @property
    def reference_count(self) -> int:
        return len(self._refs)

    def established(self, value: object) -> Optional[Tuple[str, str]]:
        """Existing spelling (display, source) for a name with the same key, if any."""
        display = clean_display(value)
        ref = self._refs.get("".join(key_tokens(display)))
        if ref is None:
            return None
        for name, (rank, _c) in ref.names.items():
            if name.casefold() == display.casefold():
                return name, _source_name(rank)
        return ref.best()

    def is_master_name(self, value: object) -> Optional[str]:
        ref = self._refs.get("".join(key_tokens(value)))
        if ref is None:
            return None
        best, source = ref.best()
        return best if source == SOURCE_MASTER else None

    def _from_mappings(
        self, raw: str, key: str, found: List[CustomerAliasMapping], method: str, scope: str
    ) -> CustomerMatch:
        values: Dict[str, CustomerAliasMapping] = {}
        for m in found:
            values.setdefault(clean_display(m.standardized_customer_name).casefold(), m)
        if len(values) == 1:
            m = next(iter(values.values()))
            return CustomerMatch(
                raw=raw, key=key, status=AUTO_MATCHED, method=method,
                suggested=m.standardized_customer_name, suggested_source=SOURCE_APPROVED,
                confidence=100.0, mapping_id=m.id, mapping_scope=scope,
            )
        alts = [
            {"name": m.standardized_customer_name, "confidence": 100.0, "source": SOURCE_APPROVED}
            for m in values.values()
        ]
        return CustomerMatch(
            raw=raw, key=key, status=NEEDS_REVIEW, method=METHOD_MAPPING_CONFLICT,
            suggested=alts[0]["name"], suggested_source=SOURCE_APPROVED,
            confidence=MEDIUM_CONFIDENCE, alternatives=alts[:_MAX_ALTERNATIVES],
        )

    def _candidates(self, tokens: List[str], own_key: str) -> List[str]:
        keys: Set[str] = set()
        for block in _blocks(tokens):
            keys |= self._blocks.get(block, set())
        keys.discard(own_key)
        if len(keys) > _MAX_CANDIDATES:
            compact = "".join(tokens)
            keys = set(sorted(keys, key=lambda k: -fuzz.ratio(compact, k))[:_MAX_CANDIDATES])
        return list(keys)

    def suggest(self, value: object, limit: int = _MAX_ALTERNATIVES) -> List[Dict[str, Any]]:
        tokens = key_tokens(value)
        key = "".join(tokens)
        if not key:
            return []
        scored: List[Tuple[float, str, str]] = []
        for cand in self._candidates(tokens, key):
            ref = self._refs[cand]
            score = similarity(tokens, ref.tokens)
            if score >= MIN_ALTERNATIVE:
                display, source = ref.best()
                scored.append((score, display, source))
        scored.sort(key=lambda item: (-item[0], _SOURCE_RANK[item[2]], item[1].casefold()))
        return [{"name": d, "confidence": s, "source": src} for s, d, src in scored[:limit]]

    def match(self, raw_value: object, distributor_id: Optional[int]) -> CustomerMatch:
        raw = clean_display(raw_value)
        tokens = key_tokens(raw)
        key = "".join(tokens)[:255]
        spaced = customer_key(raw)
        if not key:
            return CustomerMatch(raw=raw, key=spaced, status=UNMAPPED, method=METHOD_NONE)

        if distributor_id is not None:
            found = self._dist.get((int(distributor_id), key))
            if found:
                return self._from_mappings(raw, spaced, found, METHOD_DISTRIBUTOR_MAPPING, "DISTRIBUTOR")
        found = self._global.get(key)
        if found:
            return self._from_mappings(raw, spaced, found, METHOD_GLOBAL_MAPPING, "GLOBAL")

        ref = self._refs.get(key)
        if ref is not None:
            ranked = ref.ranked()
            best_rank = ranked[0][1]
            top = [item for item in ranked if item[1] == best_rank]
            dominant = top[0][0]
            if len(top) == 1 or dominant.casefold() == raw.casefold():
                return CustomerMatch(
                    raw=raw, key=spaced, status=AUTO_MATCHED,
                    method=METHOD_MASTER_EXACT if best_rank == 0 else METHOD_EXISTING_EXACT,
                    suggested=dominant, suggested_source=_source_name(best_rank), confidence=100.0,
                )
            # Several established spellings of the same name: a human picks one.
            alts = [{"name": d, "confidence": 95.0, "source": _source_name(r)} for d, r, _c in top]
            return CustomerMatch(
                raw=raw, key=spaced, status=NEEDS_REVIEW, method=METHOD_EXACT_AMBIGUOUS,
                suggested=dominant, suggested_source=_source_name(best_rank), confidence=95.0,
                alternatives=alts[:_MAX_ALTERNATIVES],
            )

        alts = self.suggest(raw)
        if alts and alts[0]["confidence"] >= MEDIUM_CONFIDENCE:
            best = alts[0]
            if len(alts) > 1 and best["confidence"] - alts[1]["confidence"] <= _AMBIGUITY_GAP:
                method = METHOD_SIMILARITY_AMBIGUOUS
            elif best["confidence"] >= HIGH_CONFIDENCE:
                method = METHOD_SIMILARITY_HIGH
            else:
                method = METHOD_SIMILARITY_MEDIUM
            return CustomerMatch(
                raw=raw, key=spaced, status=NEEDS_REVIEW, method=method,
                suggested=best["name"], suggested_source=best["source"],
                confidence=float(best["confidence"]), alternatives=alts,
            )
        return CustomerMatch(
            raw=raw, key=spaced, status=UNMAPPED, method=METHOD_NONE,
            confidence=float(alts[0]["confidence"]) if alts else 0.0, alternatives=alts,
        )

    # ------------------------------------------------------- autocomplete
    def search(self, query: object, limit: int = 20) -> List[Dict[str, Any]]:
        needle = clean_display(query).casefold()
        rows: List[Tuple[int, int, int, str]] = []
        for ref in self._refs.values():
            for display, (rank, count) in ref.names.items():
                folded = display.casefold()
                if needle and needle not in folded:
                    continue
                rows.append((0 if folded.startswith(needle) else 1, rank, -count, display))
        rows.sort(key=lambda r: (r[0], r[1], r[2], r[3].casefold()))
        out = [{"name": d, "source": _source_name(rank)} for _p, rank, _c, d in rows[:limit]]
        if needle and len(out) < limit:
            seen = {o["name"].casefold() for o in out}
            for alt in self.suggest(query, limit=limit):
                if alt["name"].casefold() not in seen and len(out) < limit:
                    out.append({"name": alt["name"], "source": alt["source"]})
                    seen.add(alt["name"].casefold())
        return out
