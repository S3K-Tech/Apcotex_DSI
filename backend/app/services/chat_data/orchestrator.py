"""Chat with Data request pipeline.

interpret → validate → resolve within scope → deterministic SQL → verified
answer → audit. Every request re-resolves and re-authorizes the previous spec.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.enums import AuditAction, AuditModule, AuditStatus
from app.llm.embedding_client import EmbeddingError, EmbeddingResult, embedding_config, embedding_usage
from app.schemas.audit import AuditTrailCreate
from app.schemas.chat_data import (
    AwaitingField,
    ChatAnswer,
    ChatClarification,
    PeriodRef,
    QueryDimension,
    QueryIntent,
    QueryMetric,
    QuerySpec,
)
from app.services.audit_service import AuditService
from app.services.chat_data.answer_builder import (
    UNIT,
    BuiltAnswer,
    build_aggregate,
    build_breakdown,
    build_caveats,
    build_compare,
    build_explain,
    build_rank,
    build_trend,
    contributions,
    filter_phrase,
    phrase_with_llm,
)
from app.services.chat_data.entity_resolver import EntityResolver, Resolution
from app.services.chat_data.intent_service import IntentService, Interpretation, RuleHints
from app.services.chat_data.knowledge import KNOWLEDGE_DOCUMENTS
from app.services.chat_data.periods import period_label, period_statement
from app.services.chat_data.query_service import ChatQueryService, ResolvedFilters, UnitAudit
from app.services.chat_data.retrieval_service import DOC_KNOWLEDGE, RetrievalService
from app.services.chat_data.scope import RESTRICTED_SEGMENT_MESSAGE, ChatScope
from app.services.llm_settings_service import LlmSettingsService
from app.utils.period_calendar import fy_short

logger = get_logger(__name__)

ALL_PERIODS_LABEL = "all available periods"
_ALL_PERIODS_OPTION = "All available periods"
_DIMENSION_OPTIONS = ["Customers", "Products", "Distributors", "Segments"]
_KIND_LABEL = {
    AwaitingField.DISTRIBUTOR: "distributor",
    AwaitingField.CUSTOMER: "customer",
    AwaitingField.PRODUCT: "product",
    AwaitingField.SEGMENT: "segment",
}
_AUDIT_STATUS = {
    "answer": AuditStatus.SUCCESS.value,
    "clarification": AuditStatus.INFO.value,
    "no_data": AuditStatus.INFO.value,
    "unsupported": AuditStatus.INFO.value,
    "restricted": AuditStatus.WARNING.value,
    "error": AuditStatus.FAILED.value,
}


def _quarter_refinements(intent: QueryIntent, spec: QuerySpec, period: Optional[PeriodRef]) -> List[str]:
    """Optional follow-ups that narrow a full-year answer to one quarter (never required)."""
    if period is None or period.quarter is not None or period.month is not None:
        return []
    if intent not in (QueryIntent.RANK, QueryIntent.BREAKDOWN, QueryIntent.AGGREGATE):
        return []
    if spec.dimension in (QueryDimension.QUARTER, QueryDimension.MONTH):
        return []
    return [f"Q{q} {fy_short(period.fy_start)}" for q in (1, 2, 3, 4)]


def _bedrock_complete(**kwargs):
    from app.llm.bedrock_client import complete

    return complete(**kwargs)


class ChatDataOrchestrator:
    def __init__(
        self,
        db: Session,
        scope: ChatScope,
        *,
        settings: Optional[Settings] = None,
        completer: Optional[Callable[..., Any]] = None,
        embedder: Optional[Callable[[str], EmbeddingResult]] = None,
    ) -> None:
        self.db = db
        self.scope = scope
        self.settings = settings or get_settings()
        self.qs = ChatQueryService(
            db, scope, statement_timeout_ms=self.settings.chat_data_statement_timeout_ms
        )
        self.llm_settings = LlmSettingsService(db)
        self.llm_enabled = bool(self.settings.chat_data_use_llm and self.llm_settings.is_enabled())
        self.completer = completer or _bedrock_complete
        self.model_id = self.llm_settings.get_model() if self.llm_enabled else None

        self.retrieval: Optional[RetrievalService] = None
        self.embedding_model: Optional[str] = None
        if self.settings.chat_semantic_search_enabled:
            try:
                self.embedding_model, _ = embedding_config()
                self.retrieval = RetrievalService(db, embedder=embedder, enabled=True)
            except EmbeddingError as exc:
                logger.warning("Chat semantic search disabled: {}", exc)
        self.resolver = EntityResolver(
            self.qs, retrieval=self.retrieval, embedding_model=self.embedding_model
        )
        self._usage: List[Tuple[str, Dict[str, int], Optional[str]]] = []

    # ---- public ---------------------------------------------------------------------

    def ask(self, question: str, previous: Optional[QuerySpec]) -> ChatAnswer:
        interpretation: Optional[Interpretation] = None
        answer: ChatAnswer
        try:
            with self.db.begin_nested():
                answer, interpretation = self._ask(question, previous)
        except SQLAlchemyError as exc:
            logger.warning("Chat with Data query failed: {}", type(exc).__name__)
            answer = ChatAnswer(
                status="error",
                message=(
                    "The data query could not be completed (it may have taken too long). "
                    "Please narrow the question, for example to one financial year or distributor."
                ),
                spec=previous,
            )
        self._record_usage()
        self._audit(answer, interpretation)
        return answer

    def suggestions(self) -> List[str]:
        if self.scope.has_no_access:
            return []
        years = self.qs.available_fiscal_years()
        if not years:
            return []
        fy = fy_short(years[0])
        out = [
            f"What was the total quantity in {fy}?",
            f"Who are the top 10 customers in {fy}?",
            f"Top 5 products in {fy}",
            f"Segment-wise breakdown for {fy}",
            f"Monthly trend for {fy}",
        ]
        if len(years) > 1:
            out.append(f"Compare {fy_short(years[1])} vs {fy}")
        distributors = self.resolver.distributors()
        if distributors:
            leader = self.qs.grouped(ResolvedFilters(), PeriodRef(fy_start=years[0]), QueryDimension.DISTRIBUTOR)
            if leader:
                out.append(f"Top 10 customers of {leader[0].label} in {fy}")
        return out

    # ---- pipeline -------------------------------------------------------------------

    def _ask(self, question: str, previous: Optional[QuerySpec]) -> Tuple[ChatAnswer, Optional[Interpretation]]:
        max_chars = int(self.settings.chat_data_max_question_chars)
        if len(question) > max_chars:
            return ChatAnswer(
                status="error",
                message=f"Please keep questions under {max_chars} characters.",
                spec=previous,
            ), None
        if self.scope.has_no_access:
            return ChatAnswer(
                status="no_data",
                message="No data access is assigned to your account. Please contact an administrator.",
                scope_note=self.scope.describe(),
            ), None

        intent = IntentService(
            use_llm=self.llm_enabled,
            completer=self.completer,
            model_id=self.model_id,
            timeout=float(self.settings.chat_data_llm_timeout),
            hints=self._hints,
            knowledge=self._knowledge(question),
        )
        interpretation = intent.interpret(question, previous)
        if interpretation.usage:
            self._usage.append(("chat_with_data_intent", interpretation.usage, interpretation.model_id))
        spec = interpretation.spec
        source = "llm" if interpretation.source == "llm" else "rules"

        if spec.intent == QueryIntent.UNSUPPORTED:
            return ChatAnswer(
                status="unsupported",
                message=spec.unsupported_reason
                or "I can only answer questions about recorded sales quantities in your accessible data.",
                interpreted_by=source,
            ), interpretation

        clarification = self._requirements(spec)
        if clarification is not None:
            clarification.interpreted_by = source
            return clarification, interpretation

        resolution = self.resolver.resolve(spec.filters)
        if resolution.status != "ok":
            answer = self._resolution_answer(spec, resolution)
            answer.interpreted_by = source
            return answer, interpretation

        answer = self._execute(spec, resolution.filters)
        answer.interpreted_by = source
        return answer, interpretation

    def _hints(self) -> RuleHints:
        return RuleHints(
            distributor_names=[name for _, name in self.resolver.distributors()],
            product_names=list(self.resolver.products().values()),
        )

    def _knowledge(self, question: str) -> List[str]:
        if self.retrieval is None or not self.embedding_model or not self.llm_enabled:
            return []
        docs = self.retrieval.search(
            question, doc_types=[DOC_KNOWLEDGE], model_id=self.embedding_model, k=3, max_distance=0.7
        )
        if self.retrieval.last_embedding is not None:
            self._usage.append(
                ("chat_with_data_retrieval", embedding_usage(self.retrieval.last_embedding), self.embedding_model)
            )
        known = {key for key, _ in KNOWLEDGE_DOCUMENTS}
        return [d.content for d in docs if d.canonical_key in known]

    def _fy_options(self, include_all: bool = True) -> List[str]:
        years = self.qs.available_fiscal_years()[:4]
        options = [fy_short(y) for y in years]
        if include_all and options:
            options.append(_ALL_PERIODS_OPTION)
        return options

    def _clarify(self, spec: QuerySpec, question: str, awaiting: AwaitingField, options: List[str]) -> ChatAnswer:
        pending = spec.model_copy(update={"awaiting": awaiting, "clarification_question": question})
        return ChatAnswer(
            status="clarification",
            message=question,
            clarification=ChatClarification(question=question, options=options),
            spec=pending,
            scope_note=self.scope.describe(),
        )

    def _requirements(self, spec: QuerySpec) -> Optional[ChatAnswer]:
        if spec.intent == QueryIntent.CLARIFY or spec.awaiting is not None:
            question = spec.clarification_question or "Could you tell me a bit more about what you need?"
            awaiting = spec.awaiting or AwaitingField.PERIOD
            options = self._fy_options() if awaiting in (AwaitingField.PERIOD, AwaitingField.SECOND_PERIOD) else []
            if spec.intent == QueryIntent.CLARIFY:
                return ChatAnswer(
                    status="clarification",
                    message=question,
                    clarification=ChatClarification(question=question, options=options),
                    spec=None if spec.awaiting is None else spec,
                )
            return self._clarify(spec, question, awaiting, options)
        if spec.intent in (QueryIntent.RANK, QueryIntent.BREAKDOWN) and spec.dimension is None:
            word = "Top" if spec.intent == QueryIntent.RANK else "Break down by"
            return self._clarify(
                spec,
                f"{word} what — customers, products, distributors or segments?",
                AwaitingField.DIMENSION,
                _DIMENSION_OPTIONS,
            )
        if spec.intent in (QueryIntent.COMPARE, QueryIntent.EXPLAIN_CHANGE) and len(spec.periods) < 2:
            if len(spec.periods) == 1:
                first = period_label(spec.periods[0])
                return self._clarify(
                    spec,
                    f"Which period should I compare {first} with?",
                    AwaitingField.SECOND_PERIOD,
                    [o for o in self._fy_options(include_all=False) if o != fy_short(spec.periods[0].fy_start)],
                )
            return self._clarify(
                spec,
                "Which two periods should I compare? For example: FY 2025-26 vs FY 2026-27, or Q1 vs Q2 FY 2026-27.",
                AwaitingField.PERIOD,
                [],
            )
        if not spec.periods and not spec.all_periods:
            return self._clarify(
                spec,
                "Which financial year should I use? Financial years run April to March. "
                "The full year is used unless you name a quarter.",
                AwaitingField.PERIOD,
                self._fy_options(),
            )
        return None

    def _resolution_answer(self, spec: QuerySpec, resolution: Resolution) -> ChatAnswer:
        if resolution.status == "restricted":
            return ChatAnswer(status="restricted", message=RESTRICTED_SEGMENT_MESSAGE, scope_note=self.scope.describe())
        field = resolution.target
        kind = _KIND_LABEL.get(field, "item")
        cleared = spec.model_copy(deep=True)
        if field in _KIND_LABEL:
            setattr(cleared.filters, field.value, None)
        if resolution.status == "ambiguous":
            if len(resolution.options) == 1:
                question = (
                    f"I couldn't find an exact {kind} match for “{resolution.mention}”. "
                    f"Did you mean {resolution.options[0]}?"
                )
            else:
                question = f"More than one {kind} matches “{resolution.mention}”. Which one do you mean?"
            return self._clarify(cleared, question, field, resolution.options)
        message = f"I couldn't find a {kind} matching “{resolution.mention}” in the data you can access."
        if field == AwaitingField.DISTRIBUTOR:
            message += f" If you meant a customer, ask about “customer {resolution.mention}”."
        return ChatAnswer(
            status="no_data",
            message=message,
            scope_note=self.scope.describe(),
            spec=cleared,
        )

    # ---- execution ------------------------------------------------------------------

    def _execute(self, spec: QuerySpec, rf: ResolvedFilters) -> ChatAnswer:
        caveats: List[str] = []
        periods: List[Optional[PeriodRef]] = list(spec.periods) if spec.periods else [None]
        labels = [period_label(p) if p else ALL_PERIODS_LABEL for p in periods]
        intent = spec.intent
        if intent == QueryIntent.AGGREGATE and len(periods) == 2:
            intent = QueryIntent.COMPARE
        if intent in (QueryIntent.RANK, QueryIntent.BREAKDOWN, QueryIntent.TREND, QueryIntent.AGGREGATE) and len(periods) > 1:
            caveats.append(f"Only the first period ({labels[0]}) was used for this question.")
            periods, labels = periods[:1], labels[:1]
        if spec.metric != QueryMetric.QUANTITY_MT and intent != QueryIntent.AGGREGATE:
            spec = spec.model_copy(update={"metric": QueryMetric.QUANTITY_MT})

        period = periods[0]
        monthly = None
        built: BuiltAnswer
        if intent == QueryIntent.AGGREGATE:
            built = build_aggregate(spec, rf, labels[0], self.qs.total(rf, period))
        elif intent in (QueryIntent.RANK, QueryIntent.BREAKDOWN):
            dim = spec.dimension or QueryDimension.CUSTOMER
            if dim == QueryDimension.MONTH:
                monthly = self.qs.monthly(rf, period)
            rows = self.qs.grouped(rf, period, dim)
            total = self.qs.total(rf, period).quantity_mt
            builder = build_rank if intent == QueryIntent.RANK else build_breakdown
            built = builder(spec, rf, labels[0], rows, total)
        elif intent == QueryIntent.COMPARE:
            totals = (self.qs.total(rf, periods[0]), self.qs.total(rf, periods[1]))
            built = build_compare(rf, (labels[0], labels[1]), totals)
        elif intent == QueryIntent.EXPLAIN_CHANGE:
            totals = (self.qs.total(rf, periods[0]), self.qs.total(rf, periods[1]))
            by_customer = contributions(
                self.qs.grouped(rf, periods[0], QueryDimension.CUSTOMER),
                self.qs.grouped(rf, periods[1], QueryDimension.CUSTOMER),
            )
            by_product = contributions(
                self.qs.grouped(rf, periods[0], QueryDimension.PRODUCT),
                self.qs.grouped(rf, periods[1], QueryDimension.PRODUCT),
            )
            built = build_explain(rf, (labels[0], labels[1]), totals, by_customer, by_product)
        elif intent == QueryIntent.TREND:
            monthly = self.qs.monthly(rf, period)
            built = build_trend(rf, labels[0], monthly)
        else:
            return ChatAnswer(status="unsupported", message="That question type is not supported yet.")

        if built.empty:
            joined = " or ".join(labels)
            return ChatAnswer(
                status="no_data",
                message=f"No sales data was found{filter_phrase(rf)} for {joined} in the data you can access.",
                periods=labels,
                filters=rf.display(),
                scope_note=self.scope.describe(),
                spec=self._resolved_spec(spec, rf),
            )

        units = UnitAudit()
        without_distributor = 0
        for p in periods:
            audit = self.qs.unit_audit(rf, p)
            units.count_unit_rows += audit.count_unit_rows
            units.unknown_unit_rows += audit.unknown_unit_rows
            without_distributor += self.qs.rows_without_distributor(rf, p)
        caveats += build_caveats(
            units=units,
            rows_without_distributor=without_distributor,
            monthly=monthly,
            product_filtered=bool(rf.product) or spec.dimension == QueryDimension.PRODUCT,
            customer_grouping=spec.dimension == QueryDimension.CUSTOMER or intent == QueryIntent.EXPLAIN_CHANGE,
        )
        caveats = list(dict.fromkeys(caveats + built.caveats))

        message = built.message
        source = "template"
        if self.llm_enabled and self.settings.chat_data_llm_phrasing:
            text, usage = phrase_with_llm(
                built,
                completer=self.completer,
                model_id=self.model_id,
                timeout=float(self.settings.chat_data_llm_timeout),
            )
            if usage:
                self._usage.append(("chat_with_data_wording", usage, self.model_id))
            if text:
                message, source = text, "llm_verified"

        coverage = self.qs.available_fiscal_years(rf) if None in periods else []
        message += "\n" + "\n".join(period_statement(p, coverage) for p in periods)

        return ChatAnswer(
            status="answer",
            message=message,
            narrative_source=source,
            facts=built.facts,
            table=built.table,
            unit=UNIT,
            periods=labels,
            filters=rf.display(),
            scope_note=self.scope.describe(),
            caveats=caveats,
            spec=self._resolved_spec(spec, rf),
            refinements=_quarter_refinements(intent, spec, period),
        )

    @staticmethod
    def _resolved_spec(spec: QuerySpec, rf: ResolvedFilters) -> QuerySpec:
        """Carry canonical names forward so follow-ups re-resolve exactly (and are re-authorized)."""
        filters = spec.filters.model_copy(
            update={
                "segment": rf.segment,
                "distributor": rf.distributor_name,
                "product": rf.product,
                "customer": rf.customer_name,
            }
        )
        return spec.model_copy(
            update={"filters": filters, "awaiting": None, "clarification_question": None, "pending_quarters": []}
        )

    # ---- logging --------------------------------------------------------------------

    def _record_usage(self) -> None:
        for purpose, usage, model in self._usage:
            try:
                with self.db.begin_nested():
                    self.llm_settings.record_usage(
                        purpose=purpose, usage=usage, model=model, actor=self.scope.user_name or None
                    )
            except SQLAlchemyError as exc:
                logger.warning("Chat usage log failed: {}", type(exc).__name__)
        self._usage.clear()

    def _audit(self, answer: ChatAnswer, interpretation: Optional[Interpretation]) -> None:
        spec = answer.spec or (interpretation.spec if interpretation else None)
        metadata: Dict[str, Any] = {
            "status": answer.status,
            "interpreted_by": answer.interpreted_by,
            "result_rows": len(answer.table.rows) if answer.table else 0,
        }
        if spec is not None:
            metadata.update(
                {
                    "intent": spec.intent.value,
                    "metric": spec.metric.value,
                    "dimension": spec.dimension.value if spec.dimension else None,
                    "periods": answer.periods or [period_label(p) for p in spec.periods],
                    "segment": spec.filters.segment if answer.status == "answer" else None,
                    "distributor_filter": bool(spec.filters.distributor),
                    "customer_filter": bool(spec.filters.customer),
                    "product_filter": bool(spec.filters.product),
                }
            )
        try:
            with self.db.begin_nested():
                AuditService(self.db).log(
                    AuditTrailCreate(
                        user_name=self.scope.user_name or "unknown",
                        user_role=self.scope.role or None,
                        user_id=self.scope.user_id,
                        action=AuditAction.SEARCHED,
                        details=f"Chat with Data question ({answer.status})",
                        module=AuditModule.CHAT_WITH_DATA.value,
                        status=_AUDIT_STATUS.get(answer.status, AuditStatus.INFO.value),
                        entity_type="chat_with_data",
                        extra_metadata=metadata,
                    )
                )
        except SQLAlchemyError as exc:
            logger.warning("Chat audit log failed: {}", type(exc).__name__)
