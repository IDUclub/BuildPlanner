import json
import re
from typing import Any, AsyncIterator

import aiohttp
from loguru import logger

_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class VLLMChatError(RuntimeError):
    """Ошибка обращения к vLLM. Наверх уходит событием `warning`/`error`, а не HTTP-статусом."""


class VLLMChatClient:
    """OpenAI-совместимый клиент vLLM — тот же контракт, что в GenPlanner и GenBuilder.

    Тело запроса: `{model, messages, stream, temperature?, response_format?}`.
    Структурный вывод — `response_format: {"type": "json_schema", "json_schema": {name, schema}}`;
    поле `strict` намеренно не отправляем, соседние сервисы тоже без него.
    """

    def __init__(self, base_url: str, model: str, temperature: float = 0.2, timeout_seconds: int = 900):
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._url = f"{base}/v1/chat/completions"
        self._model = model
        self._temperature = temperature
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    def _payload(self, messages: list[dict[str, str]], stream: bool, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": overrides.get("model") or self._model,
            "temperature": overrides.get("temperature", self._temperature),
            "messages": messages,
            "stream": stream,
        }
        if "response_format" in overrides:
            payload["response_format"] = overrides["response_format"]
        return payload

    async def stream_chat(self, messages: list[dict[str, str]], **overrides: Any) -> AsyncIterator[str]:
        """Отдаёт дельты текста по мере генерации.

        Читаем только `delta.content`: рассуждающие модели шлют ещё `reasoning_content`,
        и он пользователю не предназначен.
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(self._url, json=self._payload(messages, True, **overrides)) as response:
                if response.status != 200:
                    raise VLLMChatError(f"vLLM ответил {response.status}: {(await response.text())[:500]}")
                async for raw_line in response.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith(_SSE_DATA_PREFIX):
                        continue
                    chunk = line[len(_SSE_DATA_PREFIX) :].strip()
                    if chunk == _SSE_DONE:
                        return
                    try:
                        frame = json.loads(chunk)
                    except json.JSONDecodeError:
                        # Один битый фрейм не повод рвать уже начатый ответ.
                        logger.warning("Пропускаю нечитаемый фрейм vLLM: {}", chunk[:200])
                        continue
                    _raise_on_payload_error(frame)
                    choices = frame.get("choices") or []
                    content = (choices[0].get("delta") or {}).get("content") if choices else None
                    if content:
                        yield content

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        """Структурный вывод через `response_format: json_schema`."""
        overrides["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": json_schema},
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(self._url, json=self._payload(messages, False, **overrides)) as response:
                if response.status != 200:
                    raise VLLMChatError(f"vLLM ответил {response.status}: {(await response.text())[:500]}")
                data = await response.json()

        _raise_on_payload_error(data)
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not content:
            raise VLLMChatError("vLLM вернул пустой ответ")
        return _loads_json_object(content)


def _raise_on_payload_error(payload: dict[str, Any]) -> None:
    """vLLM сообщает об ошибке полем `error` — и в потоке тоже, со статусом 200."""
    error = payload.get("error")
    if error:
        raise VLLMChatError(str(error))


def _loads_json_object(content: str) -> dict[str, Any]:
    """Терпит обёртку вокруг JSON: модели любят завернуть ответ в ```json."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(content)
        if match is None:
            logger.error("vLLM вернул не JSON: {}", content[:200])
            raise VLLMChatError("vLLM вернул неразбираемый JSON") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error("vLLM вернул неразбираемый JSON: {}", exc)
            raise VLLMChatError("vLLM вернул неразбираемый JSON") from exc

    if not isinstance(parsed, dict):
        raise VLLMChatError("vLLM вернул не объект")
    return parsed
