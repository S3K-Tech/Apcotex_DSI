"""Chat with Data: QuerySpec validation, rule parsing, verifier, embedding client (no DB)."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.chat_data import (
    AwaitingField,
    ChatAskRequest,
    ChatFact,
    PeriodRef,
    QueryDimension,
    QueryIntent,
    QueryMetric,
    QuerySpec,
    SortDirection,
)
from app.services.chat_data.answer_builder import BuiltAnswer, verify_narrative
from app.services.chat_data.intent_service import IntentService, RuleHints, RuleParser, extract_periods

# ---- QuerySpec validation -----------------------------------------------------------


def test_query_spec_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"intent": "rank", "sql": "SELECT 1"})
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"intent": "rank", "filters": {"segment_ids": [1]}})


def test_query_spec_rejects_unknown_enum_values():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"intent": "drop_table"})
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"intent": "rank", "metric": "revenue"})


@pytest.mark.parametrize("raw,expected", [(500, 50), (0, 1), (-3, 1), ("7", 7), ("abc", 10)])
def test_limit_is_clamped(raw, expected):
    assert QuerySpec(intent=QueryIntent.RANK, limit=raw).limit == expected


def test_period_validation():
    with pytest.raises(ValidationError):
        PeriodRef(fy_start=2026, quarter=5)
    with pytest.raises(ValidationError):
        PeriodRef(fy_start=2026, quarter=1, month=4)
    with pytest.raises(ValidationError):
        QuerySpec(intent=QueryIntent.COMPARE, periods=[PeriodRef(fy_start=y) for y in (2024, 2025, 2026)])


def test_ask_request_limits():
    with pytest.raises(ValidationError):
        ChatAskRequest(question="   ")
    with pytest.raises(ValidationError):
        ChatAskRequest(question="x" * 2001)
    with pytest.raises(ValidationError):
        ChatAskRequest(question="hi", role="admin")


# ---- period extraction --------------------------------------------------------------


def test_fy_and_quarter_binding():
    per = extract_periods("Compare Q1 FY 2025-26 vs Q1 FY 2026-27")
    assert per.periods == [PeriodRef(fy_start=2025, quarter=1), PeriodRef(fy_start=2026, quarter=1)]
    per = extract_periods("Q1 vs Q2 FY 2026-27")
    assert per.periods == [PeriodRef(fy_start=2026, quarter=1), PeriodRef(fy_start=2026, quarter=2)]
    per = extract_periods("sales in FY26-27")
    assert per.periods == [PeriodRef(fy_start=2026)]


def test_month_maps_to_indian_fy():
    assert extract_periods("March 2026").periods == [PeriodRef(fy_start=2025, month=3)]
    assert extract_periods("April 2026").periods == [PeriodRef(fy_start=2026, month=4)]


def test_quarter_without_fy_requires_clarification():
    spec = RuleParser().parse("Top customers in Q1")
    assert spec.awaiting == AwaitingField.PERIOD
    assert spec.pending_quarters == [1]
    follow = RuleParser().parse("FY 2026-27", spec)
    assert follow.periods == [PeriodRef(fy_start=2026, quarter=1)]
    assert follow.intent == QueryIntent.RANK and follow.awaiting is None


def test_relative_and_single_year_periods_are_not_guessed():
    assert RuleParser().parse("Total sales last year").awaiting == AwaitingField.PERIOD
    spec = RuleParser().parse("Total sales in FY26")
    assert spec.awaiting == AwaitingField.PERIOD and not spec.periods


# ---- intents ------------------------------------------------------------------------


def test_rule_intents():
    p = RuleParser(RuleHints(distributor_names=["Alpha Traders Pvt Ltd"]))
    rank = p.parse("Top 5 customers of Alpha Traders in FY 2026-27")
    assert rank.intent == QueryIntent.RANK and rank.dimension == QueryDimension.CUSTOMER
    assert rank.limit == 5 and rank.filters.distributor == "Alpha Traders Pvt Ltd"
    low = p.parse("Lowest products in FY 2026-27")
    assert low.direction == SortDirection.ASC and low.dimension == QueryDimension.PRODUCT
    assert p.parse("Segment-wise breakdown for FY 2026-27").intent == QueryIntent.BREAKDOWN
    assert p.parse("Monthly trend for FY 2026-27").intent == QueryIntent.TREND
    assert p.parse("Compare FY 2025-26 vs FY 2026-27").intent == QueryIntent.COMPARE
    assert p.parse("Why did sales change between Q1 and Q2 FY 2026-27?").intent == QueryIntent.EXPLAIN_CHANGE
    count = p.parse("How many customers bought in FY 2026-27?")
    assert count.intent == QueryIntent.AGGREGATE and count.metric == QueryMetric.CUSTOMER_COUNT


@pytest.mark.parametrize(
    "question",
    [
        "What was the revenue in FY 2026-27?",
        "Forecast next quarter sales",
        "Delete all sales records",
        "What is the weather today",
    ],
)
def test_unsupported_questions(question):
    assert RuleParser().parse(question).intent == QueryIntent.UNSUPPORTED


def test_unknown_capitalised_name_is_kept_for_scoped_resolution():
    spec = RuleParser(RuleHints(distributor_names=["Alpha Traders"])).parse(
        "Top customers of Gamma Secret Corp in FY 2026-27"
    )
    assert spec.filters.distributor == "Gamma Secret Corp"


def test_customer_for_distributor_with_initials_is_not_a_customer_filter():
    p = RuleParser(RuleHints(distributor_names=["A.B. BROTHERS", "A.B.BROTHERS"]))
    first = p.parse("Top rank customer for A.B brothers distributor")
    assert first.filters.customer is None and not first.periods
    pending = first.model_copy(update={"awaiting": AwaitingField.PERIOD})
    spec = p.parse("FY 2026-27", pending)
    assert spec.intent == QueryIntent.RANK and spec.dimension == QueryDimension.CUSTOMER
    assert spec.filters.customer is None
    assert spec.filters.distributor in {"A.B. BROTHERS", "A.B.BROTHERS"}
    assert spec.periods == [PeriodRef(fy_start=2026)]


@pytest.mark.parametrize(
    "question,customer",
    [
        ("Sales of customer A.B. Traders in FY 2026-27", "A.B. Traders"),
        ("Sales of customer A.B. Traders Pvt. Ltd.", "A.B. Traders Pvt. Ltd"),
        ("Sales of customer Acme Corp?", "Acme Corp"),
        ("Top customer in FY 2026-27", None),
    ],
)
def test_customer_mention_extraction(question, customer):
    assert RuleParser().filters(question).customer == customer


def test_follow_up_overrides_only_what_changed():
    p = RuleParser(RuleHints(distributor_names=["Alpha Traders"]))
    first = p.parse("Top 5 customers of Alpha Traders in FY 2026-27")
    follow = p.parse("What about Q2 FY 2026-27?", first)
    assert follow.intent == QueryIntent.RANK and follow.limit == 5
    assert follow.filters.distributor == "Alpha Traders"
    assert follow.periods == [PeriodRef(fy_start=2026, quarter=2)]
    products = p.parse("now by products", first)
    assert products.dimension == QueryDimension.PRODUCT


# ---- Bedrock interpretation (stubbed) ---------------------------------------------


def _completion(text):
    return SimpleNamespace(
        text=text,
        model_id="stub-model",
        usage=lambda: {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def test_llm_valid_json_is_used():
    spec = {"intent": "rank", "dimension": "product", "periods": [{"fy_start": 2026}], "limit": 3}
    svc = IntentService(use_llm=True, completer=lambda **_: _completion(json.dumps(spec)))
    out = svc.interpret("Top 3 products in FY 2026-27", None)
    assert out.source == "llm" and out.spec.limit == 3 and out.spec.dimension == QueryDimension.PRODUCT


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        json.dumps({"intent": "rank", "sql": "DROP TABLE x"}),
        json.dumps({"intent": "explode"}),
    ],
)
def test_llm_invalid_output_falls_back_to_rules(text):
    svc = IntentService(use_llm=True, completer=lambda **_: _completion(text))
    out = svc.interpret("Top 5 customers in FY 2026-27", None)
    assert out.source == "rules" and out.spec.intent == QueryIntent.RANK and out.spec.limit == 5


def test_llm_failure_falls_back_to_rules():
    from app.llm.bedrock_client import BedrockError

    def boom(**_):
        raise BedrockError("unavailable")

    out = IntentService(use_llm=True, completer=boom).interpret("Total quantity in FY 2026-27", None)
    assert out.source == "rules" and out.spec.intent == QueryIntent.AGGREGATE


def test_llm_cannot_invent_a_financial_year():
    spec = {"intent": "aggregate", "periods": [{"fy_start": 2026, "quarter": 1}]}
    svc = IntentService(use_llm=True, completer=lambda **_: _completion(json.dumps(spec)))
    out = svc.interpret("Total sales in Q1", None)
    assert out.spec.periods == [] and out.spec.awaiting == AwaitingField.PERIOD


# ---- verifier -----------------------------------------------------------------------


def _built():
    return BuiltAnswer(
        message="Total quantity in FY 2026–27 was 103.000 MT, across 4 customers and 5 products.",
        facts=[ChatFact(key="q", label="Total", value=103.0, unit="MT", display="103.000 MT")],
        periods=["FY 2026–27"],
        names=["Acme Papers"],
    )


def test_verifier_accepts_grounded_wording():
    assert verify_narrative("In FY 2026–27 the total quantity was 103.000 MT across 4 customers.", _built())


@pytest.mark.parametrize(
    "text",
    [
        "Total quantity in FY 2026–27 was 140.000 MT.",
        "Quantity rose because of strong demand.",
        "Top customer was Zenith Holdings with 103.000 MT.",
    ],
)
def test_verifier_rejects_invented_numbers_names_and_causes(text):
    assert not verify_narrative(text, _built())


# ---- embedding client (stubbed Bedrock runtime) -----------------------------------


def _runtime(vector):
    class _Rt:
        def invoke_model(self, **kwargs):
            body = json.loads(kwargs["body"])
            assert body["dimensions"] == 1024 and body["normalize"] is True
            return {"body": io.BytesIO(json.dumps({"embedding": vector, "inputTextTokenCount": 4}).encode())}

    return _Rt()


def test_embedding_client_validates_dimensions():
    from app.llm.embedding_client import EmbeddingError, embed_text

    ok = embed_text("Paper segment", client=_runtime([0.1] * 1024))
    assert len(ok.vector) == 1024 and ok.input_tokens == 4
    with pytest.raises(EmbeddingError):
        embed_text("Paper segment", client=_runtime([0.1] * 512))


def test_embedding_config_rejects_mismatched_dimensions(monkeypatch):
    from app.core.config import get_settings
    from app.llm import embedding_client

    patched = get_settings().model_copy(update={"bedrock_embedding_dimensions": 512})
    monkeypatch.setattr(embedding_client, "get_settings", lambda: patched)
    with pytest.raises(embedding_client.EmbeddingError):
        embedding_client.embedding_config()
