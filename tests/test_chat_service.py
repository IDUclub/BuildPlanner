from typing import Any

import pytest

from app.chat.chat_service import ChatService
from app.chat.dto.chat_dto import ChatTurnDTO


class FakePipeline:
    def __init__(self, published: dict[str, Any]):
        self.published = published

    async def stream(self, *_args: Any):
        yield {"type": "scenario_published", **self.published}


class FakeStorage:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def create_chat(self, *_args: Any, **_kwargs: Any) -> str:
        return "chat-1"

    async def add_message(
        self, _chat_id: str, role: str, parts: list[dict[str, Any]], *_args: Any, **_kwargs: Any
    ) -> str:
        self.messages.append((role, parts[0]["payload"]["text"]))
        return "message-1"


async def _assistant_text(published: dict[str, Any]) -> str:
    storage = FakeStorage()
    service = ChatService(FakePipeline(published), llm_client=None, chat_storage=storage)
    async for _ in service.stream(835, ChatTurnDTO(user_query="создай мастер-план территории"), "token"):
        pass
    return next(text for role, text in storage.messages if role == "assistant")


@pytest.mark.asyncio
async def test_reply_promises_scores_only_for_an_announced_scenario():
    text = await _assistant_text({"project_id": 900, "scenario_id": 777, "notified": True})
    assert "сценарием 777" in text
    assert "по нему считаются оценки" in text


@pytest.mark.asyncio
async def test_reply_names_the_scenario_even_when_scoring_was_not_started():
    text = await _assistant_text({"project_id": 900, "scenario_id": 777, "notified": False})
    assert "сценарием 777" in text
    assert "расчёт оценок не запущен" in text
