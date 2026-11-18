"""OpenAI and Google Gemini adapters behind ``bedrock_client.complete``.

Both return ``BedrockCompletion`` and raise ``BedrockError`` subclasses so every
existing caller keeps its contract. Keys come from settings (``.env``) only.
Error messages are written here and never include provider text that could echo
a key. There is no fallback to another provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.bedrock_client import (
    _DEFAULT_NETWORK_TIMEOUT,
    _MAX_RETRIES,
    BedrockCompletion,
    BedrockError,
)
from app.llm.model_registry import (
    GEMINI,
    OPENAI,
    PROVIDER_KEY_ENV,
    PROVIDER_LABELS,
    display_name_for,
    model_provider,
    resolve_model_id,
)

logger = get_logger(__name__)


class LlmProviderError(BedrockError):
    """Safe, credential-free provider failure."""

    def __init__(self, message: str, *, retryable: bool = False, status: str = "Failed") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LlmConfigurationError(LlmProviderError):
    """The selected provider is missing its key or SDK."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status="Not Configured")


@dataclass
class _Reply:
    text: str
    input_tokens: int
    output_tokens: int


def api_key_for(provider: str) -> str:
    settings = get_settings()
    secret = settings.openai_api_key if provider == OPENAI else settings.gemini_api_key
    return (secret.get_secret_value() if secret is not None else "").strip()


def is_configured(provider: str) -> bool:
    return bool(api_key_for(provider))


def _require_key(provider: str) -> str:
    key = api_key_for(provider)
    if not key:
        raise LlmConfigurationError(
            f"{PROVIDER_LABELS[provider]} is selected but {PROVIDER_KEY_ENV[provider]} is not set. "
            "Add it to backend/.env and restart the backend."
        )
    return key


def _network_timeout(timeout: Optional[float]) -> float:
    """Same ceiling rule as the Bedrock path."""
    if timeout is not None:
        return float(timeout)
    configured = float(get_settings().bedrock_timeout or 0) or 0.0
    return configured if configured >= 120 else _DEFAULT_NETWORK_TIMEOUT


def _call_openai(
    api_key: str, *, system: str, user: str, model_id: str, timeout: float, max_tokens: Optional[int] = None
) -> _Reply:
    try:
        import openai
        from openai import OpenAI
    except ImportError:
        raise LlmConfigurationError(
            "The openai package is not installed. Run: pip install -r requirements.txt"
        ) from None

    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": user})
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_completion_tokens=max(int(max_tokens or 0), get_settings().openai_max_output_tokens),
        )
    except openai.AuthenticationError:
        raise LlmProviderError(
            "OpenAI rejected the API key. Check OPENAI_API_KEY and restart the backend.",
            status="Authentication Failed",
        ) from None
    except openai.PermissionDeniedError:
        raise LlmProviderError(
            f"OpenAI denied access to model '{model_id}' for this API key.",
            status="Access Denied",
        ) from None
    except openai.NotFoundError:
        raise LlmProviderError(
            f"OpenAI model '{model_id}' was not found or is not available to this API key. "
            "Set OPENAI_MODELS to a supported model id.",
            status="Model Unavailable",
        ) from None
    except openai.RateLimitError:
        raise LlmProviderError(
            "OpenAI rate limit or quota exceeded.", retryable=True, status="Rate Limited"
        ) from None
    except openai.APITimeoutError:
        raise LlmProviderError(
            "OpenAI request timed out.", retryable=True, status="Connection Timeout"
        ) from None
    except openai.APIConnectionError:
        raise LlmProviderError(
            "Could not reach the OpenAI API.", retryable=True, status="Connection Timeout"
        ) from None
    except openai.BadRequestError as exc:
        code = getattr(exc, "code", None) or "bad_request"
        param = getattr(exc, "param", None)
        detail = f"{code}, param={param}" if param else str(code)
        raise LlmProviderError(
            f"OpenAI rejected the request for model '{model_id}' ({detail})."
        ) from None
    except openai.APIStatusError as exc:
        status_code = int(getattr(exc, "status_code", 0) or 0)
        raise LlmProviderError(
            f"OpenAI request failed (HTTP {status_code}).", retryable=status_code >= 500
        ) from None
    except openai.OpenAIError:
        raise LlmProviderError("OpenAI request failed.") from None

    choices = list(getattr(response, "choices", None) or [])
    if not choices:
        raise LlmProviderError("OpenAI returned an empty response.")
    choice = choices[0]
    message = getattr(choice, "message", None)
    text = str(getattr(message, "content", None) or "").strip()
    if not text:
        if getattr(choice, "finish_reason", None) == "length":
            raise LlmProviderError(
                "OpenAI reached the output token limit before returning text. "
                "Increase OPENAI_MAX_OUTPUT_TOKENS."
            )
        if getattr(message, "refusal", None):
            raise LlmProviderError("OpenAI refused the request.")
        raise LlmProviderError("OpenAI returned no text content.")
    usage = getattr(response, "usage", None)
    return _Reply(
        text=text,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _gemini_status_error(exc: Any, model_id: str) -> LlmProviderError:
    code = int(getattr(exc, "code", 0) or 0)
    status = str(getattr(exc, "status", "") or "")
    folded = f"{status} {getattr(exc, 'message', '') or ''}".lower()
    if code in (401, 403) or "api key" in folded or "api_key" in folded:
        if code == 403 and "api key" not in folded:
            return LlmProviderError(
                f"Gemini denied access to model '{model_id}' for this API key.",
                status="Access Denied",
            )
        return LlmProviderError(
            "Gemini rejected the API key. Check GEMINI_API_KEY and restart the backend.",
            status="Authentication Failed",
        )
    if code == 404:
        return LlmProviderError(
            f"Gemini model '{model_id}' was not found or is not supported. "
            "Set GEMINI_MODELS to a supported model id.",
            status="Model Unavailable",
        )
    if code == 429:
        return LlmProviderError(
            "Gemini rate limit or quota exceeded.", retryable=True, status="Rate Limited"
        )
    if code >= 500:
        return LlmProviderError(f"Gemini server error (HTTP {code}).", retryable=True)
    return LlmProviderError(f"Gemini rejected the request (HTTP {code} {status}).".strip())


def _call_gemini(
    api_key: str, *, system: str, user: str, model_id: str, timeout: float, max_tokens: Optional[int] = None
) -> _Reply:
    try:
        import httpx
        from google import genai
        from google.genai import errors, types
    except ImportError:
        raise LlmConfigurationError(
            "The google-genai package is not installed. Run: pip install -r requirements.txt"
        ) from None

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout * 1000)),
    )
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=0.0,
        max_output_tokens=max(int(max_tokens or 0), get_settings().gemini_max_output_tokens),
    )
    try:
        response = client.models.generate_content(model=model_id, contents=user, config=config)
    except errors.APIError as exc:
        raise _gemini_status_error(exc, model_id) from None
    except httpx.TimeoutException:
        raise LlmProviderError(
            "Gemini request timed out.", retryable=True, status="Connection Timeout"
        ) from None
    except httpx.TransportError:
        raise LlmProviderError(
            "Could not reach the Gemini API.", retryable=True, status="Connection Timeout"
        ) from None

    text = str(getattr(response, "text", None) or "").strip()
    if not text:
        candidates = list(getattr(response, "candidates", None) or [])
        reason = str(getattr(candidates[0], "finish_reason", "") or "") if candidates else ""
        if "MAX_TOKENS" in reason:
            raise LlmProviderError(
                "Gemini reached the output token limit before returning text. "
                "Increase GEMINI_MAX_OUTPUT_TOKENS."
            )
        if not candidates:
            raise LlmProviderError("Gemini returned an empty response.")
        raise LlmProviderError("Gemini returned no text content.")
    usage = getattr(response, "usage_metadata", None)
    output = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    return _Reply(
        text=text,
        input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
        output_tokens=output,
    )


_CALLERS: dict[str, Callable[..., _Reply]] = {OPENAI: _call_openai, GEMINI: _call_gemini}


def complete_api(
    provider: str,
    *,
    system: str,
    user: str,
    model_id: str,
    timeout: Optional[float] = None,
    reason: str = "",
    max_tokens: Optional[int] = None,
) -> BedrockCompletion:
    """One completion on OpenAI or Gemini with the Bedrock retry policy."""
    if provider not in _CALLERS:
        raise LlmConfigurationError(f"Unsupported LLM provider '{provider}'.")
    key = _require_key(provider)
    limit = _network_timeout(timeout)
    started = time.perf_counter()
    for attempt in range(_MAX_RETRIES + 1):
        try:
            reply = _CALLERS[provider](
                key, system=system, user=user, model_id=model_id, timeout=limit, max_tokens=max_tokens
            )
            break
        except LlmProviderError as exc:
            if not exc.retryable or attempt >= _MAX_RETRIES:
                raise
            logger.warning(
                "{} retry | attempt={} | model={} | err={}",
                PROVIDER_LABELS[provider],
                attempt + 1,
                model_id,
                exc,
            )
    result = BedrockCompletion(
        text=reply.text,
        model_id=model_id,
        region="",
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        latency_ms=int(round((time.perf_counter() - started) * 1000)),
    )
    logger.info(
        "LLM Provider: {}\nModel: {}\nLatency: {} ms\nInput Tokens: {}\nOutput Tokens: {}\nReason: {}",
        PROVIDER_LABELS[provider],
        result.model_id,
        result.latency_ms,
        result.input_tokens,
        result.output_tokens,
        reason or "",
    )
    return result


def probe_api(model: str, message: str, *, timeout: float = 20.0) -> dict:
    """Health check for OpenAI / Gemini models. Same shape as the Bedrock probe. Never raises."""
    provider = model_provider(model)
    resolved = resolve_model_id(model)
    stamp = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    text = (message or "Hello").strip() or "Hello"
    try:
        key = _require_key(provider)
        reply = _CALLERS[provider](key, system="", user=text, model_id=resolved, timeout=float(timeout))
        status, answer = "Connected", reply.text or f"{PROVIDER_LABELS[provider]} connection successful."
    except LlmProviderError as exc:
        status, answer = exc.status, str(exc)
    except Exception:  # noqa: BLE001
        status, answer = "Failed", f"{PROVIDER_LABELS[provider]} did not complete the health check."
    latency = int(round((time.perf_counter() - started) * 1000))
    logger.info(
        "{} health check | status={} | model={} | latency_ms={}",
        PROVIDER_LABELS[provider],
        status,
        resolved,
        latency,
    )
    return {
        "status": status,
        "reply": answer,
        "latency_ms": latency,
        "model": display_name_for(resolved),
        "model_id": resolved,
        "region": PROVIDER_LABELS[provider],
        "timestamp": stamp,
    }
