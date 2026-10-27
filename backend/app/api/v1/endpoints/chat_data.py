"""Chat with Data API. Scope is always resolved server-side from the session user."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.dependencies.rbac import RequestUser, RequireUser, data_scope_for_user
from app.exceptions import AppException
from app.schemas.chat_data import ChatAnswer, ChatAskRequest, ChatStatusResponse
from app.schemas.common import DataResponse
from app.services.chat_data.orchestrator import ChatDataOrchestrator
from app.services.chat_data.scope import ChatScope
from app.services.llm_settings_service import LlmSettingsService

router = APIRouter(prefix="/chat-data", tags=["Chat with Data"])

_WINDOW_SECONDS = 60.0
_requests: Dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _require_enabled() -> None:
    if not get_settings().chat_data_enabled:
        raise AppException(
            "Chat with Data is not enabled on this server.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="CHAT_DATA_DISABLED",
        )


def _rate_limit(user: RequestUser) -> None:
    limit = int(get_settings().chat_data_rate_limit_per_minute)
    key = str(user.user_id or user.name)
    now = time.monotonic()
    with _lock:
        bucket = _requests[key]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            raise AppException(
                "Too many questions in a short time. Please wait a minute and try again.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code="CHAT_DATA_RATE_LIMITED",
            )
        bucket.append(now)


def _scope(user: RequestUser, db: Session) -> ChatScope:
    role = getattr(user.role, "value", str(user.role or ""))
    return ChatScope.from_data_scope(
        data_scope_for_user(user, db),
        user_id=user.user_id,
        role=role,
        user_name=user.name or "",
    )


@router.get("/status", summary="Chat with Data availability and suggested questions")
def chat_status(
    current: RequireUser,
    db: Session = Depends(get_db),
) -> DataResponse[ChatStatusResponse]:
    settings = get_settings()
    if not settings.chat_data_enabled:
        return DataResponse(data=ChatStatusResponse(enabled=False))
    orchestrator = ChatDataOrchestrator(db, _scope(current, db), settings=settings)
    return DataResponse(
        data=ChatStatusResponse(
            enabled=True,
            llm_enabled=bool(settings.chat_data_use_llm and LlmSettingsService(db).is_enabled()),
            semantic_search_enabled=orchestrator.retrieval is not None,
            max_question_chars=settings.chat_data_max_question_chars,
            suggestions=orchestrator.suggestions(),
        )
    )


@router.post("/ask", summary="Ask a question about sales data in your scope")
def ask(
    payload: ChatAskRequest,
    current: RequireUser,
    db: Session = Depends(get_db),
) -> DataResponse[ChatAnswer]:
    _require_enabled()
    _rate_limit(current)
    orchestrator = ChatDataOrchestrator(db, _scope(current, db))
    answer = orchestrator.ask(payload.question, payload.previous_spec)
    return DataResponse(data=answer)
