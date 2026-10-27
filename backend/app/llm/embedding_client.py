"""Bedrock text embeddings (InvokeModel). Separate from the Converse generation client.

Credentials come from the IAM role chain, as for ``bedrock_client``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ReadTimeoutError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.bedrock_client import get_bedrock_client
from app.llm.model_registry import embedding_model_for

logger = get_logger(__name__)

# Must equal the vector column dimension in the chat_embeddings migration.
EMBEDDING_DIMENSIONS = 1024
_MAX_INPUT_CHARS = 8000
_MAX_RETRIES = 1
_RETRYABLE = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


class EmbeddingError(Exception):
    """Embedding model unavailable, misconfigured, or returned an unusable vector."""


@dataclass
class EmbeddingResult:
    vector: List[float]
    model_id: str
    input_tokens: int
    latency_ms: int


def embedding_config() -> tuple[str, int]:
    """(model_id, dimensions) validated against the registry and the table dimension."""
    settings = get_settings()
    model = embedding_model_for(settings.bedrock_embedding_model)
    if model is None:
        raise EmbeddingError("BEDROCK_EMBEDDING_MODEL is not a supported embedding model")
    dims = int(settings.bedrock_embedding_dimensions)
    if dims not in model.dimensions:
        raise EmbeddingError(f"{model.display_name} does not support {dims} dimensions")
    if dims != EMBEDDING_DIMENSIONS:
        raise EmbeddingError(
            f"BEDROCK_EMBEDDING_DIMENSIONS must be {EMBEDDING_DIMENSIONS} to match chat_embeddings"
        )
    return model.model_id, dims


def embed_text(text: str, *, client: Any = None) -> EmbeddingResult:
    """Embed one text. Bounded retry; raises ``EmbeddingError`` on any failure."""
    model_id, dims = embedding_config()
    settings = get_settings()
    payload = " ".join((text or "").split())[:_MAX_INPUT_CHARS]
    if not payload:
        raise EmbeddingError("Nothing to embed")
    region = (settings.bedrock_region or settings.aws_region or "ap-south-1").strip()
    runtime = client or get_bedrock_client(region, float(settings.bedrock_embedding_timeout))
    body = json.dumps({"inputText": payload, "dimensions": dims, "normalize": True})
    attempt = 0
    while True:
        started = time.perf_counter()
        try:
            response = runtime.invoke_model(
                modelId=model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            raw = response["body"].read() if hasattr(response.get("body"), "read") else response.get("body")
            data = json.loads(raw)
            break
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _RETRYABLE and attempt < _MAX_RETRIES:
                attempt += 1
                time.sleep(0.5 * attempt)
                continue
            raise EmbeddingError(f"Embedding request failed ({code or 'client error'})") from exc
        except (NoCredentialsError, ReadTimeoutError, BotoCoreError) as exc:
            raise EmbeddingError(f"Embedding request failed ({type(exc).__name__})") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Embedding response was not valid JSON") from exc
    vector = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(vector, list) or len(vector) != dims:
        raise EmbeddingError("Embedding response has the wrong shape")
    try:
        floats = [float(v) for v in vector]
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("Embedding response contains non-numeric values") from exc
    return EmbeddingResult(
        vector=floats,
        model_id=model_id,
        input_tokens=int(data.get("inputTextTokenCount") or 0),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def embedding_usage(result: EmbeddingResult) -> dict[str, int]:
    return {
        "prompt_tokens": result.input_tokens,
        "completion_tokens": 0,
        "input_tokens": result.input_tokens,
        "output_tokens": 0,
        "total_tokens": result.input_tokens,
    }


def try_embed(text: str) -> Optional[EmbeddingResult]:
    try:
        return embed_text(text)
    except EmbeddingError as exc:
        logger.warning("Embedding unavailable: {}", exc)
        return None
