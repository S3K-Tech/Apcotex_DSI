"""Resolve segment / distributor / product / customer mentions inside the caller's scope.

Candidate lists come only from rows the caller may see, so a mention of an
unauthorized distributor, customer or product resolves exactly like a
non-existent one (no existence leak). Segments are the exception: naming a
segment outside the caller's assignment returns the restricted message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler
from sqlalchemy import distinct, func, or_, select

from app.constants.business_segments import BUSINESS_SEGMENTS, normalize_business_segment
from app.models.product_alias_mapping import ProductAliasMapping
from app.models.sales_record import SalesRecord
from app.repositories.sales_record_repository import company_expr
from app.schemas.chat_data import AwaitingField, QueryFilters
from app.services.chat_data.query_service import ChatQueryService, ResolvedFilters
from app.services.chat_data.retrieval_service import (
    DOC_DISTRIBUTOR,
    DOC_PRODUCT,
    DOC_PRODUCT_ALIAS,
    RetrievalService,
)
from app.services.product_standardization import normalize_product_key, prefer_display_name
from app.services.sales_insights_service import _customer_identity_expr
from app.utils.distributor_name import normalize_distributor_name

_MAX_OPTIONS = 6
_MAX_CUSTOMERS = 20000
_FUZZY_DISTRIBUTOR = 88
_FUZZY_PRODUCT = 90
_FUZZY_CUSTOMER = 90

ResolutionStatus = Literal["ok", "restricted", "not_found", "ambiguous"]


@dataclass
class Resolution:
    status: ResolutionStatus
    filters: ResolvedFilters = field(default_factory=ResolvedFilters)
    target: Optional[AwaitingField] = None
    mention: Optional[str] = None
    options: List[str] = field(default_factory=list)
    approximate: bool = False


def _words(text: str) -> List[str]:
    return re.findall(r"[A-Z0-9]+", (text or "").upper().replace("&", " AND "))


def _strong_typo_match(mention: str, mention_key: str, name: str, name_key: str) -> bool:
    """A misspelling of the name's leading words ("Geol" → GOEL MINERALS, "Chowdry Rubber" → CHOWDHRY RUBBER CO.).

    Every typed word must closely match the name word in the same position, so a shared
    generic word ("Rubber", "Industries") alone never produces a suggestion.
    """
    typed, words = _words(mention), _words(name)
    if len(mention_key) >= 4 and typed and len(typed) <= len(words):
        sims = [JaroWinkler.normalized_similarity(t, w) for t, w in zip(typed, words)]
        if min(sims) >= 0.85 and sum(sims) / len(sims) >= 0.9 and len(typed[0]) >= 3:
            return True
    if len(mention_key) >= 6 and fuzz.ratio(mention_key, name_key[: len(mention_key)]) >= 90:
        return True
    return False


def _customer_key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _dedupe(values: Sequence) -> list:
    return list(dict.fromkeys(values))


class EntityResolver:
    def __init__(
        self,
        qs: ChatQueryService,
        *,
        retrieval: Optional[RetrievalService] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        self.qs = qs
        self.db = qs.db
        self.scope = qs.scope
        self.retrieval = retrieval
        self.embedding_model = embedding_model
        self._distributors: Optional[List[Tuple[int, str]]] = None
        self._products: Optional[Dict[str, str]] = None
        self._segments: Optional[List[str]] = None

    # ---- authorized universes -----------------------------------------------------

    def distributors(self) -> List[Tuple[int, str]]:
        """(id, company) for distributors with in-scope active sales."""
        if self._distributors is None:
            q = self.qs.scoped(
                select(SalesRecord.distributor_id, func.min(company_expr()))
            ).group_by(SalesRecord.distributor_id)
            self._distributors = [
                (int(i), " ".join(str(name or "").split()))
                for i, name in self.db.execute(q).all()
                if i is not None and name
            ]
        return self._distributors

    def products(self) -> Dict[str, str]:
        """Canonical key → display name for in-scope products."""
        if self._products is None:
            q = self.qs.scoped(select(distinct(SalesRecord.product)))
            out: Dict[str, str] = {}
            for raw in self.db.scalars(q).all():
                name = " ".join(str(raw or "").split())
                key = normalize_product_key(name)
                if key:
                    out[key] = prefer_display_name(out.get(key), name)
            self._products = out
        return self._products

    def segments(self) -> List[str]:
        if self._segments is None:
            q = self.qs.scoped(select(distinct(SalesRecord.segment)))
            found = [" ".join(str(s or "").split()) for s in self.db.scalars(q).all()]
            self._segments = sorted({s for s in found if s}, key=str.casefold)
        return self._segments

    def customers(self, rf: ResolvedFilters) -> Dict[str, str]:
        ident = _customer_identity_expr()
        q = (
            self.qs.scoped(select(ident, func.min(SalesRecord.customer_name)), rf)
            .where(ident.is_not(None))
            .group_by(ident)
            .limit(_MAX_CUSTOMERS)
        )
        return {
            str(key): " ".join(str(name or "").split())
            for key, name in self.db.execute(q).all()
            if key
        }

    # ---- resolution ---------------------------------------------------------------

    def resolve(self, filters: QueryFilters) -> Resolution:
        rf = ResolvedFilters()
        if filters.segment:
            outcome = self._segment(filters.segment)
            if outcome.status != "ok":
                return outcome
            rf.segment = outcome.filters.segment
        if filters.distributor:
            outcome = self._distributor(filters.distributor)
            if outcome.status != "ok":
                return outcome
            rf.distributor_id = outcome.filters.distributor_id
            rf.distributor_name = outcome.filters.distributor_name
        if filters.product:
            outcome = self._product(filters.product)
            if outcome.status != "ok":
                return outcome
            rf.product = outcome.filters.product
        if filters.location:
            outcome = self._location(filters.location, rf)
            if outcome.status != "ok":
                return outcome
            rf.location = outcome.filters.location
        if filters.customer:
            outcome = self._customer(filters.customer, rf)
            if outcome.status != "ok":
                return outcome
            rf.customer_key = outcome.filters.customer_key
            rf.customer_name = outcome.filters.customer_name
        return Resolution("ok", rf)

    def _location(self, mention: str, rf: ResolvedFilters) -> Resolution:
        q = self.qs.scoped(select(distinct(SalesRecord.location)), rf)
        universe = sorted(
            {" ".join(str(v or "").split()) for v in self.db.scalars(q).all()} - {""}, key=str.casefold
        )
        key = " ".join(mention.split()).casefold()
        exact = [loc for loc in universe if loc.casefold() == key]
        if exact:
            return Resolution("ok", ResolvedFilters(location=exact[0]))
        matches = [loc for loc in universe if len(key) >= 3 and key in loc.casefold()]
        if len(matches) == 1:
            return Resolution("ok", ResolvedFilters(location=matches[0]))
        if matches:
            return Resolution(
                "ambiguous", target=AwaitingField.LOCATION, mention=mention, options=matches[:_MAX_OPTIONS]
            )
        return Resolution("not_found", target=AwaitingField.LOCATION, mention=mention)

    def _segment(self, mention: str) -> Resolution:
        canonical = normalize_business_segment(mention)
        known = next((s for s in BUSINESS_SEGMENTS if s.casefold() == canonical.casefold()), None)
        if known is None:
            known = next((s for s in self.segments() if s.casefold() == canonical.casefold()), None)
        if known is None:
            singular = canonical.casefold().rstrip("s")
            known = next((s for s in self.segments() if s.casefold().rstrip("s") == singular), None)
        if known is None:
            return Resolution("not_found", target=AwaitingField.SEGMENT, mention=mention)
        if not self.scope.segment_allowed(known):
            return Resolution("restricted", target=AwaitingField.SEGMENT, mention=mention)
        return Resolution("ok", ResolvedFilters(segment=known))

    def _distributor(self, mention: str) -> Resolution:
        universe = self.distributors()
        key = normalize_distributor_name(mention)
        if not key:
            return Resolution("not_found", target=AwaitingField.DISTRIBUTOR, mention=mention)
        keyed = [(i, name, normalize_distributor_name(name)) for i, name in universe]
        matches = [(i, n) for i, n, k in keyed if k == key]
        if not matches and len(key) >= 4:
            matches = [(i, n) for i, n, k in keyed if key in k or (len(k) >= 4 and k in key)]
        approximate = not matches
        if not matches and self.retrieval and self.embedding_model and universe:
            allowed = [i for i, _ in universe]
            hits = self.retrieval.search(
                mention,
                doc_types=[DOC_DISTRIBUTOR],
                model_id=self.embedding_model,
                allowed_ids=allowed,
                k=3,
                max_distance=0.35,
            )
            by_id = dict(universe)
            matches = [(h.canonical_id, by_id[h.canonical_id]) for h in hits if h.canonical_id in by_id]
        if not matches:
            folded = mention.casefold()
            matches = [
                (i, n) for i, n, k in keyed
                if fuzz.token_set_ratio(folded, n.casefold()) >= _FUZZY_DISTRIBUTOR
                or _strong_typo_match(mention, key, n, k)
            ]
        matches = _dedupe(matches)
        # A semantic or fuzzy guess could be a different distributor: the user confirms it.
        if len(matches) == 1 and not approximate:
            i, n = matches[0]
            return Resolution("ok", ResolvedFilters(distributor_id=i, distributor_name=n))
        if matches:
            names = sorted({n for _, n in matches}, key=str.casefold)[:_MAX_OPTIONS]
            return Resolution(
                "ambiguous", target=AwaitingField.DISTRIBUTOR, mention=mention, options=names,
                approximate=approximate,
            )
        return Resolution("not_found", target=AwaitingField.DISTRIBUTOR, mention=mention)

    def _product(self, mention: str) -> Resolution:
        universe = self.products()
        key = normalize_product_key(mention)
        if key in universe:
            return Resolution("ok", ResolvedFilters(product=universe[key]))
        alias_keys = self._alias_keys(mention)
        alias_hits = [k for k in alias_keys if k in universe]
        if len(alias_hits) == 1:
            return Resolution("ok", ResolvedFilters(product=universe[alias_hits[0]]))
        matches: List[str] = list(alias_hits)
        if not matches and len(key) >= 3:
            padded = f" {key} "
            matches = [k for k in universe if padded in f" {k} " or f" {k} " in padded]
        if not matches and self.retrieval and self.embedding_model and universe:
            hits = self.retrieval.search(
                mention,
                doc_types=[DOC_PRODUCT, DOC_PRODUCT_ALIAS],
                model_id=self.embedding_model,
                allowed_keys=list(universe),
                allowed_scope_distributor_ids=[i for i, _ in self.distributors()],
                k=3,
                max_distance=0.35,
            )
            matches = [h.canonical_key for h in hits if h.canonical_key in universe]
        if not matches:
            matches = [k for k in universe if fuzz.WRatio(key, k) >= _FUZZY_PRODUCT]
        matches = _dedupe(matches)
        if len(matches) == 1:
            return Resolution("ok", ResolvedFilters(product=universe[matches[0]]))
        if len(matches) > 1:
            names = sorted({universe[k] for k in matches}, key=str.casefold)[:_MAX_OPTIONS]
            return Resolution("ambiguous", target=AwaitingField.PRODUCT, mention=mention, options=names)
        return Resolution("not_found", target=AwaitingField.PRODUCT, mention=mention)

    def _alias_keys(self, mention: str) -> List[str]:
        """Approved alias → canonical key. Distributor-specific aliases only within scope."""
        allowed_ids = [i for i, _ in self.distributors()]
        q = select(ProductAliasMapping.normalized_product_name).where(
            ProductAliasMapping.is_active.is_(True),
            func.lower(ProductAliasMapping.original_product_name) == mention.strip().lower(),
            or_(
                ProductAliasMapping.distributor_id.is_(None),
                ProductAliasMapping.distributor_id.in_(allowed_ids or [-1]),
            ),
        )
        return _dedupe([normalize_product_key(n) for n in self.db.scalars(q).all() if n])

    def _customer(self, mention: str, rf: ResolvedFilters) -> Resolution:
        universe = self.customers(rf)
        key = _customer_key(mention)
        if key in universe:
            return Resolution("ok", ResolvedFilters(customer_key=key, customer_name=universe[key]))
        matches: List[str] = []
        if len(key) >= 3:
            matches = [k for k in universe if key in k]
        if not matches:
            scored = [(fuzz.token_set_ratio(key, k), k) for k in universe]
            fuzzy = [k for score, k in sorted(scored, reverse=True) if score >= _FUZZY_CUSTOMER]
            if fuzzy:
                names = _dedupe(universe[k] for k in fuzzy)[:_MAX_OPTIONS]
                return Resolution(
                    "ambiguous", target=AwaitingField.CUSTOMER, mention=mention, options=names, approximate=True
                )
        if len(matches) == 1:
            k = matches[0]
            return Resolution("ok", ResolvedFilters(customer_key=k, customer_name=universe[k]))
        if len(matches) > 1:
            names = sorted({universe[k] for k in matches}, key=str.casefold)[:_MAX_OPTIONS]
            return Resolution("ambiguous", target=AwaitingField.CUSTOMER, mention=mention, options=names)
        return Resolution("not_found", target=AwaitingField.CUSTOMER, mention=mention)
