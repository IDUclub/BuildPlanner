import json
import re
from typing import Any, AsyncIterator

import aiohttp
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from loguru import logger

_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_EMPTY_CONTENT_ATTEMPTS = 2


class VLLMChatError(RuntimeError):
    """Ошибка обращения к vLLM. Наверх уходит событием `warning`/`error`, а не HTTP-статусом."""


class VLLMEmptyContentError(VLLMChatError):
    """Нет текста и пригодного структурного ответа в канале рассуждений."""


class VLLMChatClient:
    """OpenAI-совместимый клиент vLLM — тот же контракт, что в GenPlanner и GenBuilder.

    Thinking выключен через шаблон Qwen, приоритет ответа в чате по умолчанию -100.
    Структурный вывод — `response_format: {"type": "json_schema", "json_schema": {name, schema}}`;
    поле `strict` намеренно не отправляем, соседние сервисы тоже без него.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        timeout_seconds: int = 900,
        *,
        chat_priority: int = -100,
    ):
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._url = f"{base}/v1/chat/completions"
        self._model = model
        self._temperature = temperature
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._chat_priority = chat_priority

    def _payload(self, messages: list[dict[str, str]], stream: bool, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": overrides.get("model") or self._model,
            "temperature": overrides.get("temperature", self._temperature),
            "messages": messages,
            "stream": stream,
            # Разбор параметров, реплика и заголовок — один live/blocking вызов.
            "priority": self._chat_priority,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if "response_format" in overrides:
            payload["response_format"] = overrides["response_format"]
        return payload

    async def stream_chat(self, messages: list[dict[str, str]], **overrides: Any) -> AsyncIterator[str]:
        """Отдаёт дельты текста по мере генерации.

        Читаем только `delta.content`: рассуждающие модели шлют ещё `reasoning_content`,
        и он пользователю не предназначен.
        """
        try:
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
                        if not isinstance(frame, dict):
                            logger.warning("Пропускаю фрейм vLLM не-объект: {}", chunk[:200])
                            continue
                        _raise_on_payload_error(frame)
                        if content := _first_choice(frame, "delta").get("content"):
                            yield content
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VLLMChatError(f"vLLM недоступен: {type(exc).__name__}: {exc}") from exc

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        """Структурный вывод; пустой ответ повторяем один раз, не повторяя HTTP-ошибки."""
        overrides["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": json_schema},
        }
        payload = self._payload(messages, False, **overrides)
        for attempt in range(1, _EMPTY_CONTENT_ATTEMPTS):
            try:
                return await self._complete_json_once(payload, json_schema)
            except VLLMEmptyContentError as exc:
                logger.warning("Пустой ответ vLLM на попытке {}, повторяю запрос: {}", attempt, exc)
        return await self._complete_json_once(payload, json_schema)

    async def _complete_json_once(self, payload: dict[str, Any], json_schema: dict[str, Any]) -> dict[str, Any]:
        """Один запрос и разбор ответа; содержимое reasoning не попадает в ошибки."""
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, json=payload) as response:
                    if response.status != 200:
                        raise VLLMChatError(f"vLLM ответил {response.status}: {(await response.text())[:500]}")
                    body = await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VLLMChatError(f"vLLM недоступен: {type(exc).__name__}: {exc}") from exc

        try:
            data = json.loads(body)
        except ValueError as exc:
            raise VLLMChatError(f"vLLM ответил не JSON: {body[:200]}") from exc
        if not isinstance(data, dict):
            raise VLLMChatError("vLLM вернул не объект")
        _raise_on_payload_error(data)
        choice = _first_choice(data)
        message = _first_choice(data, "message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # Восстанавливаем только целый JSON, соответствующий запрошенной схеме.
            # Промежуточный план или оборванный ответ не становятся командой пайплайну.
            if choice.get("finish_reason") == "stop":
                for field in ("reasoning", "reasoning_content"):
                    reasoning = message.get(field)
                    if not isinstance(reasoning, str) or not reasoning.strip():
                        continue
                    try:
                        parsed = json.loads(reasoning)
                        if not isinstance(parsed, dict):
                            continue
                        validate_json_schema(parsed, json_schema)
                    except (ValueError, JsonSchemaValidationError):
                        continue
                    logger.warning("Структурный ответ vLLM восстановлен из {} после проверки схемы", field)
                    return parsed
            raise VLLMEmptyContentError(f"vLLM вернул пустой ответ: finish_reason={choice.get('finish_reason')!r}")
        return _loads_json_object(content)


def _first_choice(payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    """`choices[0]` либо его поле `key`; пустой словарь, если форма ответа не та."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {}
    if key is None:
        return choices[0]
    part = choices[0].get(key)
    return part if isinstance(part, dict) else {}


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
