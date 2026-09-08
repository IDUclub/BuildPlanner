"""Форма запросов в ChatStorage. Сверена с клиентами GenPlanner и GenBuilder."""

from typing import Any

import pytest

from app.common.chat_storage.chat_storage_client import ChatStorageClient


class FakeHandler:
    def __init__(self, response: dict[str, Any] | None = None):
        self.response = response if response is not None else {"chat_id": "c-1"}
        self.calls: list[dict[str, Any]] = []

    async def post(self, path, json_data=None, params=None, headers=None):
        self.calls.append({"path": path, "json": json_data, "headers": headers})
        return self.response

    async def get(self, path, params=None, headers=None):
        self.calls.append({"path": path, "headers": headers})
        return self.response


class FakeTokens:
    async def get_token(self) -> str:
        return "service-token"


@pytest.fixture(name="handler")
def handler_fixture() -> FakeHandler:
    return FakeHandler()


@pytest.fixture(name="client")
def client_fixture(handler: FakeHandler) -> ChatStorageClient:
    return ChatStorageClient(handler, FakeTokens())


@pytest.mark.asyncio
async def test_scenario_id_is_a_top_level_field(client, handler):
    """В `metadata` сценарий не считается: чат не привяжется к сценарию проекта."""
    await client.create_chat("Заголовок", "user-1", scenario_id=835)
    body = handler.calls[-1]["json"]
    assert body["scenario_id"] == 835
    assert body["project_id"] is None


@pytest.mark.asyncio
async def test_metadata_is_never_null(client, handler):
    """Поле объявлено необнуляемым — `null` отдаётся как 422."""
    await client.create_chat("Заголовок", "user-1")
    assert handler.calls[-1]["json"]["metadata"] == {}


@pytest.mark.asyncio
async def test_user_id_travels_in_its_own_header(client, handler):
    """При сервисном токене ChatStorage узнаёт пользователя только из `X-User-Id`."""
    await client.create_chat("Заголовок", "user-1")
    headers = handler.calls[-1]["headers"]
    assert headers["X-User-Id"] == "user-1"
    assert headers["Authorization"] == "Bearer service-token"


@pytest.mark.asyncio
async def test_message_carries_parts_and_metadata(client, handler):
    await client.add_message("c-1", "assistant", [ChatStorageClient.text_part("готово")], "user-1", {"residents": 5000})
    body = handler.calls[-1]["json"]
    assert body["role"] == "assistant"
    assert body["parts"] == [{"kind": "text", "payload": {"text": "готово"}}]
    assert body["metadata"] == {"residents": 5000}


@pytest.mark.asyncio
async def test_history_is_flattened_for_the_llm():
    chat = {
        "messages": [
            {"role": "user", "parts": [{"kind": "text", "payload": {"text": "привет"}}]},
            {"role": "assistant", "parts": [{"kind": "file", "payload": {"url": "..."}}]},
            {"role": "assistant", "content": "плоское сообщение"},
        ]
    }
    assert ChatStorageClient.build_llm_history(chat) == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "плоское сообщение"},
    ]
