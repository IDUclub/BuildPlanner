import json
from typing import Any, AsyncIterator

import aiohttp
from loguru import logger


class VLLMChatError(RuntimeError):
    """Ошибка обращения к vLLM. Наверх уходит событием `warning`/`error`, а не HTTP-статусом."""


class VLLMChatClient:
    """OpenAI-совместимый клиент vLLM — тот же контракт, что в GenPlanner и GenBuilder."""

    def __init__(self, base_url: str, model: str, temperature: float = 0.2, timeout_seconds: int = 900):
        base = base_url.rstrip("/")
        self._url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
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
        """Отдаёт дельты текста по мере генерации."""
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(self._url, json=self._payload(messages, True, **overrides)) as response:
                if response.status != 200:
                    raise VLLMChatError(f"vLLM ответил {response.status}: {(await response.text())[:500]}")
                async for raw_line in response.content:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:") :].strip()
                    if chunk == "[DONE]":
                        return
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"]
                    except (json.JSONDecodeError, KeyError, IndexError) as exc:
                        raise VLLMChatError(f"Некорректный SSE-фрейм от vLLM: {exc}") from exc
                    content = delta.get("content")
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
            "json_schema": {"name": "answer", "schema": json_schema, "strict": True},
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(self._url, json=self._payload(messages, False, **overrides)) as response:
                if response.status != 200:
                    raise VLLMChatError(f"vLLM ответил {response.status}: {(await response.text())[:500]}")
                data = await response.json()
        try:
            return json.loads(data["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.error("vLLM вернул неразбираемый JSON: {}", exc)
            raise VLLMChatError("vLLM вернул неразбираемый JSON") from exc
