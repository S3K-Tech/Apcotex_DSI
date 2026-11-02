"""Chat with Data request, structured query spec, and answer schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_RESULT_LIMIT = 50
DEFAULT_RESULT_LIMIT = 10
MAX_MENTION_CHARS = 200


class QueryIntent(str, Enum):
    AGGREGATE = "aggregate"
    RANK = "rank"
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


class QueryDimension(str, Enum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    DISTRIBUTOR = "distributor"
    SEGMENT = "segment"
    QUARTER = "quarter"
    MONTH = "month"


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

    @field_validator("segment", "distributor", "customer", "product", mode="before")
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
    filters: QueryFilters = Field(default_factory=QueryFilters)
    periods: List[PeriodRef] = Field(default_factory=list, max_length=2)
    all_periods: bool = False
    direction: SortDirection = SortDirection.DESC
    limit: int = DEFAULT_RESULT_LIMIT
    clarification_question: Optional[str] = Field(default=None, max_length=300)
    unsupported_reason: Optional[str] = Field(default=None, max_length=300)
    awaiting: Optional[AwaitingField] = None
    pending_quarters: List[int] = Field(default_factory=list, max_length=2)

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


class ChatClarification(BaseModel):
    question: str
    options: List[str] = Field(default_factory=list)


class ChatAnswer(BaseModel):
    status: ChatStatus
    message: str
    narrative_source: Literal["template", "llm_verified"] = "template"
    facts: List[ChatFact] = Field(default_factory=list)
    table: Optional[ChatTable] = None
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
