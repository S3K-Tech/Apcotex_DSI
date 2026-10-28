"""Central LLM model registry.

Model ids stay in this module. The rest of the application uses display names
or ``resolve_model_id()``. The first Bedrock entry is the default.

Bedrock models are fixed below. OpenAI and Gemini model ids come from the
``OPENAI_MODELS`` / ``GEMINI_MODELS`` environment variables (comma-separated).
"""

from __future__ import annotations

from dataclasses import dataclass

BEDROCK = "bedrock"
OPENAI = "openai"
GEMINI = "gemini"

PROVIDER_LABELS = {BEDROCK: "AWS Bedrock", OPENAI: "OpenAI", GEMINI: "Google Gemini"}
# Environment variable holding each API provider's key. Bedrock uses the IAM role.
PROVIDER_KEY_ENV = {OPENAI: "OPENAI_API_KEY", GEMINI: "GEMINI_API_KEY"}

DEFAULT_OPENAI_MODELS = "gpt-5.4"
DEFAULT_GEMINI_MODELS = "gemini-2.5-flash"


@dataclass(frozen=True)
class BedrockModel:
    display_name: str
    model_id: str
    input_usd_per_1m: float
    output_usd_per_1m: float
    provider: str = "converse"


_MODELS: tuple[BedrockModel, ...] = (
    BedrockModel("MiniMax M2", "minimax.minimax-m2", 0.50, 1.50, provider="converse"),
    BedrockModel("GLM 4.7", "zai.glm-4.7", 0.50, 1.50, provider="converse"),
    BedrockModel("GLM 4.7 Flash", "zai.glm-4.7-flash", 0.20, 0.80, provider="converse"),
)


def default_model() -> BedrockModel:
    return _MODELS[0]


def default_model_id() -> str:
    return default_model().model_id


def public_model_names() -> list[str]:
    return [model.display_name for model in _MODELS]


def _split_ids(raw: str | None, fallback: str) -> list[str]:
    ids = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    return ids or [fallback]


def _api_models() -> tuple[BedrockModel, ...]:
    """OpenAI / Gemini models from settings. Pricing is not tracked (cost shows 0)."""
    from app.core.config import get_settings

    settings = get_settings()
    models: list[BedrockModel] = []
    for provider, raw, fallback in (
        (OPENAI, settings.openai_models, DEFAULT_OPENAI_MODELS),
        (GEMINI, settings.gemini_models, DEFAULT_GEMINI_MODELS),
    ):
        for model_id in _split_ids(raw, fallback):
            models.append(BedrockModel(model_id, model_id, 0.0, 0.0, provider=provider))
    return tuple(models)


def _all_models() -> tuple[BedrockModel, ...]:
    return _MODELS + _api_models()


def _match(value: str) -> BedrockModel | None:
    text = value.strip()
    if not text:
        return None
    folded = text.casefold()
    for model in _all_models():
        if text == model.model_id or folded == model.display_name.casefold():
            return model
    return None


def model_provider(value: str | None) -> str:
    """``bedrock`` / ``openai`` / ``gemini`` for a model. Unknown values are Bedrock."""
    found = _match(value or "")
    if found is None or found.provider == "converse":
        return BEDROCK
    return found.provider


def models_for_provider(provider: str) -> list[BedrockModel]:
    if provider == BEDROCK:
        return list(_MODELS)
    return [model for model in _api_models() if model.provider == provider]


def model_names_for(provider: str) -> list[str]:
    return [model.display_name for model in models_for_provider(provider)]


def default_model_for(provider: str) -> BedrockModel:
    models = models_for_provider(provider)
    return models[0] if models else default_model()


def resolve_model_id(value: str | None) -> str:
    """Map a display name or model id to the Bedrock id. Unknown values use the default."""
    found = _match(value or "")
    return found.model_id if found else default_model_id()


def is_known_model(value: str | None) -> bool:
    return _match(value or "") is not None


def display_name_for(value: str | None) -> str:
    """Friendly name for the UI. Unknown stored values use the default name."""
    found = _match(value or "")
    if found:
        return found.display_name
    embedding = embedding_model_for(value)
    return embedding.display_name if embedding else default_model().display_name


def rates_for(value: str | None) -> tuple[float, float]:
    found = _match(value or "")
    if found is None:
        embedding = embedding_model_for(value)
        if embedding:
            return embedding.input_usd_per_1m, 0.0
        found = default_model()
    return found.input_usd_per_1m, found.output_usd_per_1m


@dataclass(frozen=True)
class EmbeddingModel:
    display_name: str
    model_id: str
    dimensions: tuple[int, ...]
    input_usd_per_1m: float


# Invoked with InvokeModel (not Converse). Kept apart from generation models.
_EMBEDDING_MODELS: tuple[EmbeddingModel, ...] = (
    EmbeddingModel(
        "Titan Text Embeddings V2",
        "amazon.titan-embed-text-v2:0",
        (256, 512, 1024),
        0.02,
    ),
)


def embedding_model_for(value: str | None) -> EmbeddingModel | None:
    text = (value or "").strip()
    if not text:
        return None
    for model in _EMBEDDING_MODELS:
        if text == model.model_id or text.casefold() == model.display_name.casefold():
            return model
    return None


def provider_for(value: str | None) -> str:
    """Return the invocation provider for a display name or model id."""
    found = _match(value or "") or default_model()
    return found.provider
