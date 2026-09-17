from typing import Any

import pytest

from app.chat.chat_service import ChatService
from app.chat.dto.chat_dto import ChatTurnDTO
from app.pipeline.geo_layers import layer_descriptor
from app.pipeline.master_plan import build_summary


class FakePipeline:
    def __init__(self, published: dict[str, Any]):
        self.published = published

    async def stream(self, *_args: Any, **_kwargs: Any):
        yield {"type": "scenario_published", **self.published}


class FilesPipeline:
    """Прогон, который отдал зоны и застройку ссылками на хранилище."""

    def __init__(self):
        self.base_url: str | None = None

    async def stream(self, *_args: Any, base_url: str | None = None, **_kwargs: Any):
        self.base_url = base_url
        for slot in ("zones", "buildings"):
            yield {
                "type": "file",
                **layer_descriptor(slot, "a" * 32, request_base_url=base_url),
            }


class FakeStorage:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.parts: dict[str, list[dict[str, Any]]] = {}

    async def create_chat(self, *_args: Any, **_kwargs: Any) -> str:
        return "chat-1"

    async def add_message(
        self, _chat_id: str, role: str, parts: list[dict[str, Any]], *_args: Any, **_kwargs: Any
    ) -> str:
        self.messages.append((role, parts[0]["payload"]["text"]))
        self.parts[role] = parts
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


@pytest.mark.asyncio
async def test_reply_says_scoring_started_partly_when_the_broker_failed_halfway():
    text = await _assistant_text(
        {"project_id": 900, "scenario_id": 777, "notified": False, "notified_events": ["scenario_zones_updated"]}
    )
    assert "сценарием 777" in text
    assert "расчёт оценок запущен частично" in text


@pytest.mark.asyncio
async def test_layers_are_saved_to_history_as_links():
    """После перезагрузки чата карту перерисовывают по ссылкам из сообщения, а не по потоку."""
    storage, pipeline = FakeStorage(), FilesPipeline()
    service = ChatService(pipeline, llm_client=None, chat_storage=storage)
    turn = ChatTurnDTO(user_query="создай мастер-план территории")
    async for _ in service.stream(835, turn, "token", base_url="http://buildplanner/"):
        pass
    assert pipeline.base_url == "http://buildplanner/"
    files = [part for part in storage.parts["assistant"] if part["kind"] == "file"]
    assert [part["payload"]["name"] for part in files] == ["zones", "buildings"]
    assert files[0]["payload"]["url"] == f"http://buildplanner/buildplanner/files/zones/{'a' * 32}"
    assert "download_url" not in files[0]["payload"]
    assert storage.parts["assistant"][0]["kind"] == "text"


class SummaryPipeline:
    """Прогон, дошедший до справки: она приходит последним событием."""

    def __init__(self, summary: dict[str, Any]):
        self.summary = summary

    async def stream(self, *_args: Any, **_kwargs: Any):
        yield {"type": "warning", "stage": "sirtep", "detail": "sirtep_skipped", "message": "Очередь пропущена."}
        yield {"type": "master_plan_summary", **self.summary}


async def _text_of(pipeline: Any) -> str:
    storage = FakeStorage()
    service = ChatService(pipeline, llm_client=None, chat_storage=storage)
    async for _ in service.stream(835, ChatTurnDTO(user_query="создай мастер-план территории"), "token"):
        pass
    return next(text for role, text in storage.messages if role == "assistant")


@pytest.mark.asyncio
async def test_summary_closes_the_saved_reply():
    """После перезагрузки чата справка читается из самого сообщения, а не из событий."""
    summary = build_summary(
        buildings={"buildings": 12, "residents": 3400},
        published={"scenario_id": 777},
        schedule={"provision": {"houses_per_period": [12], "periods": [1], "provided_per_period": [0.9]}},
        provision={"periods": [1], "provision": [{"школа": 0.9}], "unbuilt_services": ["депо"]},
    )
    text = await _text_of(SummaryPipeline(summary))
    assert "Очередь пропущена." in text
    assert text.index("Очередь пропущена.") < text.index("Что построено")
    assert "депо" in text
