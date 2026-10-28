"""Provider selection: AWS Bedrock (default), OpenAI, Google Gemini.

Every provider SDK call is mocked. No network access and no API credits are used.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openpyxl import Workbook
from pydantic import SecretStr

OPENAI_KEY = "sk-test-SECRET-openai-0000"
GEMINI_KEY = "AIza-test-SECRET-gemini-0000"


@pytest.fixture()
def api_settings(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(OPENAI_KEY))
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(GEMINI_KEY))
    monkeypatch.setattr(settings, "openai_models", "gpt-5.4,gpt-5.4-mini")
    monkeypatch.setattr(settings, "gemini_models", "gemini-2.5-flash")
    return settings


@pytest.fixture()
def no_bedrock(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("Bedrock must not be called")

    monkeypatch.setattr("app.llm.bedrock_client.get_bedrock_client", _fail)


class _FakeOpenAI:
    """Stand-in for ``openai.OpenAI``. ``script`` yields responses or exceptions."""

    instances: list = []
    script: list = []
    calls: list = []

    def __init__(self, **kwargs):
        _FakeOpenAI.instances.append(kwargs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        _FakeOpenAI.calls.append(kwargs)
        item = _FakeOpenAI.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _openai_reply(text: str, *, finish: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, refusal=None), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
    )


@pytest.fixture()
def fake_openai(monkeypatch):
    import openai

    _FakeOpenAI.instances, _FakeOpenAI.script, _FakeOpenAI.calls = [], [], []
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return _FakeOpenAI


class _FakeGemini:
    instances: list = []
    script: list = []
    calls: list = []

    def __init__(self, **kwargs):
        _FakeGemini.instances.append(kwargs)
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        _FakeGemini.calls.append(kwargs)
        item = _FakeGemini.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _gemini_reply(text: str):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=3, thoughts_token_count=2),
    )


@pytest.fixture()
def fake_gemini(monkeypatch):
    from google import genai

    _FakeGemini.instances, _FakeGemini.script, _FakeGemini.calls = [], [], []
    monkeypatch.setattr(genai, "Client", _FakeGemini)
    return _FakeGemini


def _openai_status_error(cls, status: int, message: str):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls(message, response=httpx.Response(status, request=request), body=None)


# ---- registry ------------------------------------------------------------------------


def test_registry_keeps_bedrock_models_and_default(api_settings):
    from app.llm.model_registry import (
        default_model_id,
        model_names_for,
        model_provider,
        public_model_names,
        resolve_model_id,
    )

    assert default_model_id() == "minimax.minimax-m2"
    assert public_model_names() == ["MiniMax M2", "GLM 4.7", "GLM 4.7 Flash"]
    assert model_names_for("bedrock") == public_model_names()
    assert model_names_for("openai") == ["gpt-5.4", "gpt-5.4-mini"]
    assert model_names_for("gemini") == ["gemini-2.5-flash"]
    assert model_provider("GLM 4.7") == "bedrock"
    assert model_provider("gpt-5.4") == "openai"
    assert model_provider("gemini-2.5-flash") == "gemini"
    assert resolve_model_id("gpt-5.4-mini") == "gpt-5.4-mini"
    assert resolve_model_id("not-a-model") == "minimax.minimax-m2"


# ---- shared complete(): routing ------------------------------------------------------


def test_bedrock_path_unchanged(monkeypatch, api_settings, fake_openai):
    from app.llm import bedrock_client

    seen = {}

    class _Client:
        def converse(self, **kwargs):
            seen.update(kwargs)
            return {
                "output": {"message": {"content": [{"text": '{"ok": true}'}]}},
                "usage": {"inputTokens": 3, "outputTokens": 2},
            }

    monkeypatch.setattr(bedrock_client, "get_bedrock_client", lambda region, timeout: _Client())
    result = bedrock_client.complete(system="SYS", user="USR", model_id="GLM 4.7", reason="t")

    assert seen == {
        "modelId": "zai.glm-4.7",
        "system": [{"text": "SYS"}],
        "messages": [{"role": "user", "content": [{"text": "USR"}]}],
        "inferenceConfig": {"temperature": 0.0, "maxTokens": 800},
    }
    assert result.text == '{"ok": true}' and result.model_id == "zai.glm-4.7"
    assert fake_openai.instances == []


def test_openai_selected_uses_openai_adapter_and_key(api_settings, fake_openai, no_bedrock):
    from app.llm.bedrock_client import BedrockCompletion, complete

    fake_openai.script = [_openai_reply('{"a": 1}')]
    result = complete(system="SYS", user="USR", model_id="gpt-5.4", timeout=12, reason="t")

    assert isinstance(result, BedrockCompletion)
    assert result.text == '{"a": 1}' and result.model_id == "gpt-5.4"
    assert result.usage()["prompt_tokens"] == 11 and result.usage()["completion_tokens"] == 5
    assert fake_openai.instances[0]["api_key"] == OPENAI_KEY
    assert fake_openai.instances[0]["max_retries"] == 0
    call = fake_openai.calls[0]
    assert call["model"] == "gpt-5.4"
    assert call["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_gemini_selected_uses_gemini_adapter_and_key(api_settings, fake_gemini, no_bedrock):
    from app.llm.bedrock_client import complete

    fake_gemini.script = [_gemini_reply('{"b": 2}')]
    result = complete(system="SYS", user="USR", model_id="gemini-2.5-flash", timeout=8)

    assert result.text == '{"b": 2}' and result.model_id == "gemini-2.5-flash"
    assert result.input_tokens == 7 and result.output_tokens == 5
    assert fake_gemini.instances[0]["api_key"] == GEMINI_KEY
    call = fake_gemini.calls[0]
    assert call["model"] == "gemini-2.5-flash" and call["contents"] == "USR"
    assert call["config"].system_instruction == "SYS"
    assert call["config"].temperature == 0.0


@pytest.mark.parametrize(
    ("setting", "model", "env_var"),
    [("openai_api_key", "gpt-5.4", "OPENAI_API_KEY"), ("gemini_api_key", "gemini-2.5-flash", "GEMINI_API_KEY")],
)
def test_missing_key_is_a_clear_configuration_error(
    monkeypatch, api_settings, fake_openai, fake_gemini, no_bedrock, setting, model, env_var
):
    from app.llm.bedrock_client import BedrockError, complete
    from app.llm.provider_clients import LlmConfigurationError

    monkeypatch.setattr(api_settings, setting, SecretStr(""))
    with pytest.raises(LlmConfigurationError) as info:
        complete(system="S", user="U", model_id=model)
    assert isinstance(info.value, BedrockError)
    assert env_var in str(info.value)
    assert fake_openai.instances == [] and fake_gemini.instances == []


# ---- adapter error handling ----------------------------------------------------------


def test_openai_invalid_key_error_never_leaks_the_key(api_settings, fake_openai, no_bedrock):
    import openai

    from app.llm.bedrock_client import complete
    from app.llm.provider_clients import LlmProviderError

    fake_openai.script = [
        _openai_status_error(openai.AuthenticationError, 401, f"Incorrect API key provided: {OPENAI_KEY}")
    ]
    with pytest.raises(LlmProviderError) as info:
        complete(system="S", user="U", model_id="gpt-5.4")
    assert OPENAI_KEY not in str(info.value)
    assert "OPENAI_API_KEY" in str(info.value)
    assert info.value.__cause__ is None
    assert len(fake_openai.calls) == 1


def test_openai_unknown_model_is_reported_not_substituted(api_settings, fake_openai, no_bedrock):
    import openai

    from app.llm.bedrock_client import complete
    from app.llm.provider_clients import LlmProviderError

    fake_openai.script = [_openai_status_error(openai.NotFoundError, 404, "model not found")]
    with pytest.raises(LlmProviderError) as info:
        complete(system="S", user="U", model_id="gpt-5.4-mini")
    assert "gpt-5.4-mini" in str(info.value) and "OPENAI_MODELS" in str(info.value)
    assert [c["model"] for c in fake_openai.calls] == ["gpt-5.4-mini"]


def test_openai_rate_limit_is_retried_then_succeeds(api_settings, fake_openai, no_bedrock):
    import openai

    from app.llm.bedrock_client import complete

    fake_openai.script = [
        _openai_status_error(openai.RateLimitError, 429, "slow down"),
        _openai_status_error(openai.RateLimitError, 429, "slow down"),
        _openai_reply('{"ok": 1}'),
    ]
    assert complete(system="S", user="U", model_id="gpt-5.4").text == '{"ok": 1}'
    assert len(fake_openai.calls) == 3


def test_openai_timeout_exhausts_retries_without_fallback(api_settings, fake_openai, no_bedrock):
    import openai

    from app.llm.bedrock_client import complete
    from app.llm.provider_clients import LlmProviderError

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    fake_openai.script = [openai.APITimeoutError(request=request) for _ in range(3)]
    with pytest.raises(LlmProviderError) as info:
        complete(system="S", user="U", model_id="gpt-5.4")
    assert "timed out" in str(info.value)
    assert len(fake_openai.calls) == 3


def test_openai_empty_output_is_a_malformed_response(api_settings, fake_openai, no_bedrock):
    from app.llm.bedrock_client import complete
    from app.llm.provider_clients import LlmProviderError

    fake_openai.script = [_openai_reply("", finish="length")]
    with pytest.raises(LlmProviderError) as info:
        complete(system="S", user="U", model_id="gpt-5.4")
    assert "OPENAI_MAX_OUTPUT_TOKENS" in str(info.value)
    assert len(fake_openai.calls) == 1


def test_gemini_invalid_key_and_unknown_model(api_settings, fake_gemini, no_bedrock):
    from google.genai import errors

    from app.llm.bedrock_client import complete
    from app.llm.provider_clients import LlmProviderError

    fake_gemini.script = [
        errors.ClientError(
            400,
            {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}},
        ),
        errors.ClientError(
            404, {"error": {"code": 404, "message": "models/x is not found", "status": "NOT_FOUND"}}
        ),
    ]
    with pytest.raises(LlmProviderError) as auth:
        complete(system="S", user="U", model_id="gemini-2.5-flash")
    assert "GEMINI_API_KEY" in str(auth.value) and GEMINI_KEY not in str(auth.value)
    with pytest.raises(LlmProviderError) as missing:
        complete(system="S", user="U", model_id="gemini-2.5-flash")
    assert "GEMINI_MODELS" in str(missing.value)


def test_probe_uses_selected_provider(api_settings, fake_gemini, no_bedrock):
    from app.llm.bedrock_client import probe

    fake_gemini.script = [_gemini_reply("Hello back")]
    result = probe("gemini-2.5-flash", "Hello")
    assert result["status"] == "Connected" and result["reply"] == "Hello back"
    assert result["model_id"] == "gemini-2.5-flash"
    assert GEMINI_KEY not in json.dumps(result)


# ---- settings persistence ------------------------------------------------------------


def test_default_settings_remain_bedrock(db, api_settings):
    from app.services.llm_settings_service import LlmSettingsService, resolve_runtime_llm

    svc = LlmSettingsService(db)
    assert svc.get_provider() == "bedrock"
    assert svc.get_model() == "minimax.minimax-m2"
    payload = svc.get_settings_payload()
    assert payload["available_models"] == ["MiniMax M2", "GLM 4.7", "GLM 4.7 Flash"]
    assert payload["available_providers"] == ["bedrock", "openai", "gemini"]
    serialized = json.dumps(payload)
    assert OPENAI_KEY not in serialized and GEMINI_KEY not in serialized
    assert payload["api_key_masked"] == ""
    assert resolve_runtime_llm(db)["provider"] == "bedrock"


def test_legacy_bedrock_model_setting_is_preserved(db, api_settings):
    from app.models.system_setting import SystemSetting
    from app.services.llm_settings_service import LlmSettingsService

    db.add(SystemSetting(key="llm_model", value="zai.glm-4.7"))
    db.flush()
    svc = LlmSettingsService(db)
    assert svc.get_provider() == "bedrock" and svc.get_model() == "zai.glm-4.7"


def test_provider_and_model_selection_persist(db, api_settings):
    from app.services.llm_settings_service import LlmSettingsService, resolve_runtime_llm

    svc = LlmSettingsService(db)
    svc.update_settings(provider="openai")
    assert svc.get_provider() == "openai" and svc.get_model() == "gpt-5.4"

    svc.update_settings(provider="openai", model="gpt-5.4-mini")
    runtime = resolve_runtime_llm(db)
    assert runtime["provider"] == "openai" and runtime["model"] == "gpt-5.4-mini"

    payload = svc.update_settings(provider="gemini", model="gemini-2.5-flash", enabled=True)
    assert payload["provider"] == "gemini" and payload["model"] == "gemini-2.5-flash"
    assert payload["available_models"] == ["gemini-2.5-flash"]

    svc.update_settings(provider="bedrock", model="GLM 4.7 Flash")
    assert svc.get_provider() == "bedrock" and svc.get_model() == "zai.glm-4.7-flash"


def test_selecting_unconfigured_provider_is_rejected(db, monkeypatch, api_settings):
    from app.exceptions import ValidationAppError
    from app.services.llm_settings_service import LlmSettingsService

    monkeypatch.setattr(api_settings, "openai_api_key", SecretStr(""))
    svc = LlmSettingsService(db)
    with pytest.raises(ValidationAppError) as info:
        svc.update_settings(provider="openai", model="gpt-5.4")
    assert "OPENAI_API_KEY" in str(info.value)
    assert svc.get_provider() == "bedrock" and svc.get_model() == "minimax.minimax-m2"


def test_model_must_belong_to_selected_provider(db, api_settings):
    from app.exceptions import ValidationAppError
    from app.services.llm_settings_service import LlmSettingsService

    svc = LlmSettingsService(db)
    with pytest.raises(ValidationAppError):
        svc.update_settings(provider="openai", model="GLM 4.7")
    with pytest.raises(ValidationAppError):
        svc.update_settings(provider="bedrock", model="gpt-5.4")
    assert svc.get_provider() == "bedrock"


def test_usage_is_attributed_to_the_serving_provider(db, api_settings):
    from app.services.llm_settings_service import LlmSettingsService

    svc = LlmSettingsService(db)
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert svc.record_usage(purpose="header_mapping", usage=usage, model="gpt-5.4", provider="bedrock").provider == "openai"
    assert svc.record_usage(purpose="header_mapping", usage=usage, model="zai.glm-4.7", provider="bedrock").provider == "bedrock"


# ---- ERP callers keep their prompts and contracts ------------------------------------


@pytest.fixture()
def quiet_usage(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.LlmSettingsService.record_usage",
        lambda self, **kwargs: None,
    )


def test_layout_analysis_routes_to_openai_with_unchanged_prompt(
    monkeypatch, api_settings, fake_openai, no_bedrock, quiet_usage
):
    from app.erp_parser.llm_header_resolver import LLMHeaderResolver
    from app.erp_parser.orchestrator.service import SEMANTIC_PROMPT

    runtime = {"enabled": True, "model": "gpt-5.4", "timeout": 5}
    monkeypatch.setattr(LLMHeaderResolver, "_runtime_settings", lambda self: runtime)
    answer = {
        "layout_type": "standard_header",
        "customer_column": "Customer Name",
        "product_column": "Item",
        "quantity_column": "Qty Out",
        "parser_hint": "header",
        "confidence": 91,
        "is_sales_table": True,
        "product_from_title": "",
    }
    fake_openai.script = [_openai_reply(json.dumps(answer))]
    mapped = LLMHeaderResolver().resolve_headers(
        ["Customer Name", "Item", "Qty In", "Qty Out"],
        [["Acme", "N745", "0", "5"]],
        sample_limit=5,
        system_prompt=SEMANTIC_PROMPT,
        extra={"reason": "test"},
    )
    assert fake_openai.calls[0]["messages"][0] == {"role": "system", "content": SEMANTIC_PROMPT}
    assert mapped["quantity_column"] == "Qty Out" and mapped["confidence"] == 91
    assert mapped["layout_type"] == "standard_header" and mapped["is_sales_table"] is True


def test_worksheet_selection_routes_to_gemini_with_unchanged_prompt(
    monkeypatch, tmp_path, api_settings, fake_gemini, no_bedrock, quiet_usage
):
    from app.erp_parser import llm_sheet_resolver
    from app.erp_parser.llm_sheet_resolver import LLMSheetResolver

    path = tmp_path / "multi.xlsx"
    wb = Workbook()
    wb.active.title = "Cover"
    wb.active["A1"] = "Distributor report"
    data = wb.create_sheet("Data")
    data.append(["Customer", "Product", "Qty"])
    data.append(["Acme", "N745", 5])
    wb.save(path)

    runtime = {"enabled": True, "model": "gemini-2.5-flash", "timeout": 5}
    monkeypatch.setattr(LLMSheetResolver, "_runtime_settings", lambda self: runtime)
    fake_gemini.script = [_gemini_reply('{"sheet_name": "Data", "confidence": 82, "reason": "headers"}')]
    name, confidence = LLMSheetResolver().pick_sheet(path)

    assert (name, confidence) == ("Data", 82.0)
    assert fake_gemini.calls[0]["config"].system_instruction == llm_sheet_resolver.SYSTEM_PROMPT


def test_quarter_detection_routes_to_openai(monkeypatch, tmp_path, api_settings, fake_openai, no_bedrock, quiet_usage):
    from app.erp_parser import quarter_detector

    path = tmp_path / "noquarter.xlsx"
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.active["A1"] = "Secondary sales statement"
    wb.save(path)

    monkeypatch.setattr(
        "app.services.llm_settings_service.resolve_runtime_llm",
        lambda db=None: {"enabled": True, "provider": "openai", "model": "gpt-5.4", "timeout": 5},
    )
    fake_openai.script = [_openai_reply('{"reporting_quarter": "FY 2025-26 • Q2", "confidence": 95}')]
    hit = quarter_detector._llm_detect_quarter(path)

    assert hit["reporting_quarter"] == "FY 2025-26 • Q2" and hit["source"] == "llm"
    assert fake_openai.calls[0]["model"] == "gpt-5.4"


def test_email_body_model_follows_provider(monkeypatch, api_settings):
    from app.erp_parser.documents import email_body

    runtime = {"enabled": True, "provider": "gemini", "model": "gemini-2.5-flash", "timeout": 5}
    monkeypatch.setattr("app.services.llm_settings_service.resolve_runtime_llm", lambda db=None: runtime)
    assert email_body._layout_model_id() == "gemini-2.5-flash"

    runtime = {"enabled": True, "provider": "bedrock", "model": "zai.glm-4.7", "timeout": 5}
    monkeypatch.setattr("app.services.llm_settings_service.resolve_runtime_llm", lambda db=None: runtime)
    assert email_body._layout_model_id() == "MiniMax M2"


def test_deterministic_parse_needs_no_llm(monkeypatch, tmp_path, api_settings, no_bedrock):
    from app.erp_parser import ERPParserService

    def _no_api(*_args, **_kwargs):
        raise AssertionError("No LLM provider may be called")

    monkeypatch.setattr("app.llm.provider_clients.complete_api", _no_api)
    path = tmp_path / "standard.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Customer Name", "Product", "Quantity"])
    ws.append(["Acme Rubber Pvt Ltd", "Apcoflex N745", 12.5])
    ws.append(["Beta Polymers Ltd", "Apcotex SR-100", 4])
    wb.save(path)

    result = ERPParserService().parse_workbook(path, allow_llm_fallback=False)
    assert result.mapping_source == "python"
    assert [row["customer_name"] for row in result.rows] == ["Acme Rubber Pvt Ltd", "Beta Polymers Ltd"]
