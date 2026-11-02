"""
Deterministic product matching against approved mappings and the active FG Master.

Priority (first hit wins):
1. approved distributor-specific mapping
2. approved global mapping
3. exact normalized match in the active FG Master
4. high-confidence similarity suggestion   (NEEDS_REVIEW)
5. medium-confidence similarity suggestion (NEEDS_REVIEW)
6. unmapped

Only 1-3 are applied automatically. Similarity results are suggestions that a
user must approve; nothing here writes to the database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fg_master import FGMasterProduct, FGMasterVersion
from app.models.product_alias_mapping import ProductAliasMapping
from app.services.product_master.normalize import (
    clean_display,
    compact_key,
    digit_groups,
    normalize_key,
)

AUTO_MATCHED = "AUTO_MATCHED"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNMAPPED = "UNMAPPED"

METHOD_DISTRIBUTOR_MAPPING = "DISTRIBUTOR_MAPPING"
METHOD_GLOBAL_MAPPING = "GLOBAL_MAPPING"
METHOD_MASTER_EXACT = "MASTER_EXACT"
METHOD_SIMILARITY_HIGH = "SIMILARITY_HIGH"
METHOD_SIMILARITY_MEDIUM = "SIMILARITY_MEDIUM"
METHOD_MAPPING_CONFLICT = "MAPPING_CONFLICT"
METHOD_NONE = "NONE"

HIGH_CONFIDENCE = 90.0
MEDIUM_CONFIDENCE = 75.0
_DIGIT_MISMATCH_CAP = 60.0
_CANDIDATE_LIMIT = 15
_MAX_ALTERNATIVES = 5


@dataclass
class MatchResult:
    raw: str
    key: str
    status: str
    method: str
    suggested: Optional[str] = None
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
            "raw_product": self.raw,
            "normalized_key": self.key,
            "status": self.status,
            "method": self.method,
            "suggested_product": self.suggested,
            "confidence": round(self.confidence, 1),
            "confidence_tier": self.confidence_tier,
            "mapping_id": self.mapping_id,
            "mapping_scope": self.mapping_scope,
            "approved": self.approved,
            "alternatives": self.alternatives,
        }


def similarity_score(query: str, candidate: str) -> float:
    """0-100 similarity between two normalized keys, strict about grade numbers."""
    q, m = normalize_key(query), normalize_key(candidate)
    if not q or not m:
        return 0.0
    qc, mc = q.replace(" ", ""), m.replace(" ", "")
    score = max(float(fuzz.token_sort_ratio(q, m)), float(fuzz.ratio(qc, mc)))
    coverage = min(len(qc) / max(len(mc), 1), 1.0)
    score = max(score, float(fuzz.token_set_ratio(q, m)) * (0.85 + 0.15 * coverage))
    if len(qc) >= 4 and qc in mc:
        score = max(score, 85.0 + 10.0 * coverage)
    q_digits = digit_groups(q)
    if q_digits and not set(q_digits) <= set(digit_groups(m)):
        # "AB 820" must never look like a strong match for "AB 850".
        score = min(score, _DIGIT_MISMATCH_CAP)
    return round(min(score, 99.0), 1)


class ProductMatcher:
    """In-memory snapshot of approved mappings + active master for one request."""

    def __init__(
        self,
        *,
        master_version: Optional[FGMasterVersion],
        master_descriptions: Iterable[str],
        mappings: Iterable[ProductAliasMapping],
    ) -> None:
        self.master_version = master_version
        self._by_key: Dict[str, str] = {}
        self._by_compact: Dict[str, set] = defaultdict(set)
        for desc in master_descriptions:
            key = normalize_key(desc)
            if not key:
                continue
            self._by_key.setdefault(key, desc)
            self._by_compact[key.replace(" ", "")].add(self._by_key[key])
        self._keys: List[str] = list(self._by_key.keys())

        self._global: Dict[str, List[ProductAliasMapping]] = defaultdict(list)
        self._dist: Dict[Tuple[int, str], List[ProductAliasMapping]] = defaultdict(list)
        for m in mappings:
            key = (m.normalized_original_name or compact_key(m.original_product_name)).replace(" ", "")
            if not key:
                continue
            if m.distributor_id is None:
                self._global[key].append(m)
            else:
                self._dist[(int(m.distributor_id), key)].append(m)

    @classmethod
    def load(cls, db: Session) -> "ProductMatcher":
        version = db.scalar(
            select(FGMasterVersion).where(FGMasterVersion.status == "active").limit(1)
        )
        descriptions: List[str] = []
        if version is not None:
            descriptions = list(
                db.scalars(
                    select(FGMasterProduct.material_description).where(
                        FGMasterProduct.version_id == version.id,
                        FGMasterProduct.is_active.is_(True),
                    )
                )
            )
        mappings = list(
            db.scalars(
                select(ProductAliasMapping).where(
                    ProductAliasMapping.is_active.is_(True),
                    ProductAliasMapping.status == "APPROVED",
                )
            )
        )
        return cls(master_version=version, master_descriptions=descriptions, mappings=mappings)

    @property
    def master_configured(self) -> bool:
        return self.master_version is not None and bool(self._keys)

    @property
    def master_descriptions(self) -> List[str]:
        return list(self._by_key.values())

    def master_contains(self, value: str) -> Optional[str]:
        return self._by_key.get(normalize_key(value))

    def _from_mappings(
        self, raw: str, key: str, found: List[ProductAliasMapping], method: str, scope: str
    ) -> MatchResult:
        values: Dict[str, ProductAliasMapping] = {}
        for m in found:
            values.setdefault(normalize_key(m.normalized_product_name), m)
        if len(values) == 1:
            m = next(iter(values.values()))
            return MatchResult(
                raw=raw,
                key=key,
                status=AUTO_MATCHED,
                method=method,
                suggested=m.normalized_product_name,
                confidence=100.0,
                mapping_id=m.id,
                mapping_scope=scope,
            )
        # Legacy rows that differ only by punctuation and disagree: ask a human.
        alts = [{"description": m.normalized_product_name, "confidence": 100.0} for m in values.values()]
        return MatchResult(
            raw=raw,
            key=key,
            status=NEEDS_REVIEW,
            method=METHOD_MAPPING_CONFLICT,
            suggested=alts[0]["description"],
            confidence=MEDIUM_CONFIDENCE,
            alternatives=alts[:_MAX_ALTERNATIVES],
        )

    def suggest(self, value: str, limit: int = _MAX_ALTERNATIVES) -> List[Dict[str, Any]]:
        key = normalize_key(value)
        if not key or not self._keys:
            return []
        candidates = {c for c, _s, _i in process.extract(key, self._keys, scorer=fuzz.WRatio, limit=_CANDIDATE_LIMIT)}
        candidates |= {
            c for c, _s, _i in process.extract(key, self._keys, scorer=fuzz.token_set_ratio, limit=_CANDIDATE_LIMIT)
        }
        qc = key.replace(" ", "")
        if len(qc) >= 4:
            candidates |= {k for k in self._keys if qc in k.replace(" ", "")}
        scored = sorted(
            ((similarity_score(key, c), self._by_key[c]) for c in candidates),
            key=lambda item: (-item[0], item[1]),
        )
        return [{"description": d, "confidence": s} for s, d in scored[:limit] if s > 0]

    def match(self, raw_value: object, distributor_id: Optional[int]) -> MatchResult:
        raw = clean_display(raw_value)
        key = normalize_key(raw)
        if not key:
            return MatchResult(raw=raw, key=key, status=UNMAPPED, method=METHOD_NONE)

        mapping_key = key.replace(" ", "")
        if distributor_id is not None:
            found = self._dist.get((int(distributor_id), mapping_key))
            if found:
                return self._from_mappings(raw, key, found, METHOD_DISTRIBUTOR_MAPPING, "DISTRIBUTOR")
        found = self._global.get(mapping_key)
        if found:
            return self._from_mappings(raw, key, found, METHOD_GLOBAL_MAPPING, "GLOBAL")

        exact = self._by_key.get(key)
        if exact is None:
            compact = self._by_compact.get(compact_key(raw))
            if compact and len(compact) == 1:
                exact = next(iter(compact))
        if exact is not None:
            return MatchResult(
                raw=raw, key=key, status=AUTO_MATCHED, method=METHOD_MASTER_EXACT,
                suggested=exact, confidence=100.0,
            )

        alts = self.suggest(raw)
        if alts and alts[0]["confidence"] >= MEDIUM_CONFIDENCE:
            best = alts[0]
            method = (
                METHOD_SIMILARITY_HIGH if best["confidence"] >= HIGH_CONFIDENCE else METHOD_SIMILARITY_MEDIUM
            )
            return MatchResult(
                raw=raw, key=key, status=NEEDS_REVIEW, method=method,
                suggested=best["description"], confidence=float(best["confidence"]),
                alternatives=alts,
            )
        return MatchResult(
            raw=raw, key=key, status=UNMAPPED, method=METHOD_NONE,
            confidence=float(alts[0]["confidence"]) if alts else 0.0,
            alternatives=alts,
        )
