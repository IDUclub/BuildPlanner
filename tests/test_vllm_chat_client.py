"""Разбор ответов vLLM. Форма запроса и потока сверена с клиентами GenPlanner и GenBuilder."""

import pytest

from app.common.llm.vllm_chat_client import VLLMChatClient, VLLMChatError, _loads_json_object, _raise_on_payload_error


def test_v1_suffix_is_not_doubled():
    assert VLLMChatClient("http://llm:8000/v1", "m")._url == "http://llm:8000/v1/chat/completions"
    assert VLLMChatClient("http://llm:8000/", "m")._url == "http://llm:8000/v1/chat/completions"


def test_payload_carries_the_openai_fields():
    payload = VLLMChatClient("http://llm:8000", "m")._payload([{"role": "user", "content": "?"}], stream=True)
    assert set(payload) == {"model", "temperature", "messages", "stream"}
    assert payload["stream"] is True


def test_json_schema_response_format_has_no_strict_flag():
    """Соседние сервисы шлют только `name` и `schema`; `strict` — лишнее расхождение."""
    client = VLLMChatClient("http://llm:8000", "m")
    payload = client._payload(
        [],
        stream=False,
        response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": {}}},
    )
    assert set(payload["response_format"]["json_schema"]) == {"name", "schema"}


def test_error_field_is_raised_even_with_status_200():
    """vLLM сообщает об ошибке телом, в том числе внутри потока."""
    with pytest.raises(VLLMChatError):
        _raise_on_payload_error({"error": {"message": "context length exceeded"}})


def test_clean_json_is_parsed():
    assert _loads_json_object('{"action": "answer"}') == {"action": "answer"}


def test_fenced_json_is_parsed():
    """Модели любят завернуть ответ в ```json — из-за этого терять прогон не стоит."""
    content = 'Вот ответ:\n```json\n{"action": "run_pipeline"}\n```'
    assert _loads_json_object(content) == {"action": "run_pipeline"}


def test_non_object_json_is_rejected():
    with pytest.raises(VLLMChatError):
        _loads_json_object("[1, 2, 3]")


def test_garbage_is_rejected():
    with pytest.raises(VLLMChatError):
        _loads_json_object("совсем не json")


async def _serve(body: str, status: int = 200):
    """Поднимает локальный vLLM-заглушку, отдающую `body` на любой POST."""
    from aiohttp import web

    async def handle(_request):
        return web.Response(text=body, status=status, content_type="application/json")

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_unreachable_vllm_is_a_chat_error_not_a_crash():
    """ClientConnectorError мимо VLLMChatError рвал SSE без события `error`."""
    client = VLLMChatClient("http://127.0.0.1:1", "m", timeout_seconds=5)
    with pytest.raises(VLLMChatError, match="недоступен"):
        await client.complete_json([], {})
    with pytest.raises(VLLMChatError, match="недоступен"):
        async for _ in client.stream_chat([]):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[]", "not json", '{"choices": "x"}', '{"choices": [{"message": null}]}'])
async def test_malformed_completion_is_a_chat_error(body):
    runner, url = await _serve(body)
    try:
        with pytest.raises(VLLMChatError):
            await VLLMChatClient(url, "m").complete_json([], {})
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_non_object_stream_frames_are_skipped():
    body = 'data: [1]\n\ndata: {"choices": []}\n\ndata: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n'
    runner, url = await _serve(body)
    try:
        chunks = [chunk async for chunk in VLLMChatClient(url, "m").stream_chat([])]
    finally:
        await runner.cleanup()
    assert chunks == ["ok"]
