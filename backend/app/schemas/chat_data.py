"""Chat with Data request, structured query spec, and answer schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_RESULT_LIMIT = 50
DEFAULT_RESULT_LIMIT = 10
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_MENTION_CHARS = 200


class QueryIntent(str, Enum):
    AGGREGATE = "aggregate"
    RANK = "rank"
    LIST = "list"
    BREAKDOWN = "breakdown"
    COMPARE = "compare"
    TREND = "trend"
    EXPLAIN_CHANGE = "explain_change"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"


class QueryMetric(str, Enum):
    QUANTITY_MT = "quantity_mt"
    CUSTOMER_COUNT = "customer_count"
    PRODUCT_COUNT = "product_count"
    # Distinct count of ``QuerySpec.dimension`` (distributors, customers, products, ...).
    ENTITY_COUNT = "entity_count"


class QueryDimension(str, Enum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    DISTRIBUTOR = "distributor"
    SEGMENT = "segment"
    LOCATION = "location"
    QUARTER = "quarter"
    MONTH = "month"
    # One row per distributor-segment pair seen in sales ("distributors with their segments").
    DISTRIBUTOR_SEGMENT = "distributor_segment"


class SortDirection(str, Enum):
    DESC = "desc"
    ASC = "asc"


class AwaitingField(str, Enum):
    """What a clarification is waiting for (drives deterministic follow-up merge)."""

    PERIOD = "period"
    SECOND_PERIOD = "second_period"
    DIMENSION = "dimension"
    DISTRIBUTOR = "distributor"
    CUSTOMER = "customer"
    PRODUCT = "product"
    SEGMENT = "segment"
    LOCATION = "location"


class PeriodRef(BaseModel):
    """Explicit Indian financial-year period (April–March). ``fy_start`` 2026 = FY 2026-27."""

    model_config = ConfigDict(extra="forbid")

    fy_start: int = Field(..., ge=2000, le=2100)
    quarter: Optional[int] = Field(default=None, ge=1, le=4)
    month: Optional[int] = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _one_granularity(self) -> "PeriodRef":
        if self.quarter is not None and self.month is not None:
            raise ValueError("Use either quarter or month, not both")
        return self


class QueryFilters(BaseModel):
    """User-supplied mentions. Resolved and authorized server-side on every request."""

    model_config = ConfigDict(extra="forbid")

    segment: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)
    distributor: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)
    customer: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)
    product: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)
    location: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)

    @field_validator("segment", "distributor", "customer", "product", "location", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = " ".join(str(value).split())
        if not text or text.lower() in {"all", "any", "none", "null"}:
            return None
        return text


class QuerySpec(BaseModel):
    """Closed, validated description of one analytical question."""

    model_config = ConfigDict(extra="forbid")

    intent: QueryIntent
    metric: QueryMetric = QueryMetric.QUANTITY_MT
    dimension: Optional[QueryDimension] = None
    # ENTITY_COUNT split by another dimension ("how many distributors in each segment").
    per: Optional[QueryDimension] = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    periods: List[PeriodRef] = Field(default_factory=list, max_length=2)
    all_periods: bool = False
    direction: SortDirection = SortDirection.DESC
    limit: int = DEFAULT_RESULT_LIMIT
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    clarification_question: Optional[str] = Field(default=None, max_length=300)
    unsupported_reason: Optional[str] = Field(default=None, max_length=300)
    awaiting: Optional[AwaitingField] = None
    pending_quarters: List[int] = Field(default_factory=list, max_length=2)
    # The single "Did you mean …?" suggestion, so a plain "yes" confirms it.
    suggestion: Optional[str] = Field(default=None, max_length=MAX_MENTION_CHARS)
    # Optional chart the user asked for (pie / bar / line). Built from authorised SQL rows.
    chart_type: Optional[Literal["pie", "bar", "line"]] = None

    @field_validator("pending_quarters")
    @classmethod
    def _valid_quarters(cls, value: List[int]) -> List[int]:
        if any(q not in (1, 2, 3, 4) for q in value):
            raise ValueError("Quarters must be 1–4")
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def _clamp_limit(cls, value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_RESULT_LIMIT
        return max(1, min(MAX_RESULT_LIMIT, number))

    @field_validator("page", mode="before")
    @classmethod
    def _clamp_page(cls, value: Any) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    @field_validator("page_size", mode="before")
    @classmethod
    def _clamp_page_size(cls, value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_PAGE_SIZE
        return max(1, min(MAX_PAGE_SIZE, number))


class ChatAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=2000)
    previous_spec: Optional[QuerySpec] = None

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = "".join(ch for ch in value if ch.isprintable() or ch in "\n\t")
        text = text.strip()
        if not text:
            raise ValueError("Question is empty")
        return text


class ChatPageRequest(BaseModel):
    """Another page of a previous "list all" answer. The spec is re-resolved and re-authorized."""

    model_config = ConfigDict(extra="forbid")

    spec: QuerySpec
    page: int = Field(..., ge=1, le=100_000)
    page_size: Optional[int] = Field(default=None, ge=1, le=MAX_PAGE_SIZE)


ChatStatus = Literal["answer", "clarification", "restricted", "no_data", "unsupported", "error"]


class ChatFact(BaseModel):
    key: str
    label: str
    value: Union[float, int, str]
    unit: Optional[str] = None
    display: str


class ChatTableColumn(BaseModel):
    key: str
    label: str
    numeric: bool = False


class ChatTable(BaseModel):
    columns: List[ChatTableColumn]
    rows: List[Dict[str, Any]]


class ChatPagination(BaseModel):
    total_records: int
    current_page: int
    page_size: int
    total_pages: int
    first_record: int
    last_record: int
    entity: str


class ChatClarification(BaseModel):
    question: str
    options: List[str] = Field(default_factory=list)


class ChatChartPoint(BaseModel):
    name: str
    value: float


class ChatChart(BaseModel):
    type: Literal["pie", "bar", "line"]
    title: str
    unit: str = "MT"
    points: List[ChatChartPoint] = Field(default_factory=list)


class ChatAnswer(BaseModel):
    status: ChatStatus
    message: str
    narrative_source: Literal["template", "llm_verified"] = "template"
    facts: List[ChatFact] = Field(default_factory=list)
    table: Optional[ChatTable] = None
    chart: Optional[ChatChart] = None
    pagination: Optional[ChatPagination] = None
    unit: Optional[str] = None
    periods: List[str] = Field(default_factory=list)
    filters: Dict[str, str] = Field(default_factory=dict)
    scope_note: Optional[str] = None
    caveats: List[str] = Field(default_factory=list)
    clarification: Optional[ChatClarification] = None
    spec: Optional[QuerySpec] = None
    interpreted_by: Literal["llm", "rules", "none"] = "none"
    refinements: List[str] = Field(default_factory=list)


class ChatStatusResponse(BaseModel):
    enabled: bool
    llm_enabled: bool = False
    semantic_search_enabled: bool = False
    max_question_chars: int = 500
    suggestions: List[str] = Field(default_factory=list)


class ChatExportRequest(BaseModel):
    """Payload to export a Chat with Data answer as an Excel (.xlsx) workbook."""

    question: Optional[str] = Field(default=None, max_length=500)
    message: str = ""
    periods: List[str] = Field(default_factory=list)
    filters: Dict[str, str] = Field(default_factory=dict)
    facts: List[ChatFact] = Field(default_factory=list)
    table: Optional[ChatTable] = None
    chart: Optional[ChatChart] = None
    pagination: Optional[ChatPagination] = None
    spec: Optional[QuerySpec] = None

