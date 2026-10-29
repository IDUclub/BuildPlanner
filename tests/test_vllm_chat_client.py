"""Разбор ответов vLLM. Форма запроса и потока сверена с клиентами GenPlanner и GenBuilder."""

import json

import pytest

from app.chat.agent.prompts import DRAFT_SCHEMA
from app.chat.chat_service import ChatService
from app.chat.dto.chat_dto import ChatTurnDTO
from app.common.llm.vllm_chat_client import VLLMChatClient, VLLMChatError, _loads_json_object, _raise_on_payload_error


def test_v1_suffix_is_not_doubled():
    assert VLLMChatClient("http://llm:8000/v1", "m")._url == "http://llm:8000/v1/chat/completions"
    assert VLLMChatClient("http://llm:8000/", "m")._url == "http://llm:8000/v1/chat/completions"


def test_payload_carries_the_openai_fields():
    payload = VLLMChatClient("http://llm:8000", "m")._payload([{"role": "user", "content": "?"}], stream=True)
    assert set(payload) == {"model", "temperature", "messages", "stream", "priority", "chat_template_kwargs"}
    assert payload["stream"] is True
    assert payload["priority"] == -100
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in payload


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


async def _serve(body: str | list[str], status: int = 200, requests: list[dict] | None = None):
    """Поднимает локальный vLLM-заглушку, отдающую `body` на любой POST."""
    from aiohttp import web

    bodies = [body] if isinstance(body, str) else body
    count = 0

    async def handle(request):
        nonlocal count
        if requests is not None:
            requests.append(await request.json())
        response_body = bodies[min(count, len(bodies) - 1)]
        count += 1
        return web.Response(text=response_body, status=status, content_type="application/json")

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


def _completion(content=None, reasoning="", field="reasoning", finish_reason="stop") -> str:
    return json.dumps(
        {"choices": [{"finish_reason": finish_reason, "message": {"content": content, field: reasoning}}]}
    )


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("content", [None, "", "  "])
async def test_structured_answer_is_recovered_from_reasoning(field, content):
    decision = {"action": "answer", "reply": "Уточните территорию.", "title": "Выбор территории", "patch": {}}
    requests = []
    runner, url = await _serve(_completion(content, json.dumps(decision), field), requests=requests)
    try:
        assert await VLLMChatClient(url, "qwen3.8-27b").complete_json([], DRAFT_SCHEMA) == decision
    finally:
        await runner.cleanup()
    assert len(requests) == 1
    assert requests[0]["priority"] == -100
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "reasoning,finish_reason",
    [
        ('Thinking: {"action": "answer", "reply": "ok"}', "stop"),
        ('{"action": "run_generation", "reply": "ok"}', "stop"),
        ('{"action": "answer"}', "stop"),
        ('{"action": "answer", "reply": 123}', "stop"),
        ('{"action": "answer", "reply": "ok", "patch": {"unexpected": true}}', "stop"),
        ('[{"action": "answer", "reply": "ok"}]', "stop"),
        ('{"action": "answer", "reply": "ok"}', "length"),
        ('{"action": "answer", "reply": "ok"}', None),
    ],
)
async def test_unusable_reasoning_retries_once(reasoning, finish_reason):
    decision = {"action": "answer", "reply": "Готово."}
    requests = []
    runner, url = await _serve(
        [_completion(reasoning=reasoning, finish_reason=finish_reason), _completion(json.dumps(decision))],
        requests=requests,
    )
    try:
        assert await VLLMChatClient(url, "qwen3.8-27b").complete_json([], DRAFT_SCHEMA) == decision
    finally:
        await runner.cleanup()
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert [request["priority"] for request in requests] == [-100, -100]


async def test_empty_retries_are_bounded_and_do_not_disclose_reasoning():
    requests = []
    runner, url = await _serve(_completion(reasoning="private reasoning text"), requests=requests)
    try:
        with pytest.raises(VLLMChatError) as error:
            await VLLMChatClient(url, "qwen3.8-27b").complete_json([], DRAFT_SCHEMA)
    finally:
        await runner.cleanup()
    assert len(requests) == 2
    assert "finish_reason='stop'" in str(error.value)
    assert "private reasoning text" not in str(error.value)


async def test_content_takes_precedence_over_reasoning():
    decision = {"action": "answer", "reply": "Готово."}
    runner, url = await _serve(_completion(json.dumps(decision), '{"action": "run_pipeline", "reply": "Запускаю."}'))
    try:
        assert await VLLMChatClient(url, "qwen3.8-27b").complete_json([], DRAFT_SCHEMA) == decision
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("priority", [-100, -80, 0])
async def test_configured_priority_reaches_completions_and_streams(priority):
    requests = []
    stream = 'data: {"choices": [{"delta": {"reasoning": "hidden"}}]}\n\ndata: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n'
    runner, url = await _serve([_completion("{}"), stream], requests=requests)
    try:
        client = VLLMChatClient(url, "qwen3.8-27b", chat_priority=priority)
        await client.complete_json([], {})
        assert [chunk async for chunk in client.stream_chat([])] == ["ok"]
    finally:
        await runner.cleanup()
    assert [request["priority"] for request in requests] == [priority, priority]
    assert all(request["chat_template_kwargs"] == {"enable_thinking": False} for request in requests)


async def test_http_errors_are_not_retried():
    requests = []
    runner, url = await _serve("bad request", status=400, requests=requests)
    try:
        with pytest.raises(VLLMChatError, match="400"):
            await VLLMChatClient(url, "qwen3.8-27b").complete_json([], DRAFT_SCHEMA)
    finally:
        await runner.cleanup()
    assert len(requests) == 1


async def test_recovered_chat_reply_does_not_fall_back_to_running_the_pipeline():
    """Ответ в reasoning не теряется: чат отвечает, не запускает запасной прогон."""
    decision = {"action": "answer", "reply": "Сервис выбирает профиль по показателям территории."}
    runner, url = await _serve(_completion(reasoning=json.dumps(decision)))

    class ForbiddenPipeline:
        async def stream(self, *_args, **_kwargs):
            pytest.fail("Вопрос пользователя не должен запускать пайплайн")
            yield {}  # pragma: no cover

    try:
        service = ChatService(ForbiddenPipeline(), VLLMChatClient(url, "qwen3.8-27b"), chat_storage=None)
        events = [
            event async for event in service.stream(845, ChatTurnDTO(user_query="Как выбирается профиль?"), "test")
        ]
    finally:
        await runner.cleanup()
    text = "".join(event["content"]["text"] for event in events if event["type"] == "chunk")
    assert text == decision["reply"]
    assert events[-1]["type"] == "done"
