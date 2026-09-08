from typing import Any

from loguru import logger

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.auth.service_token import ServiceTokenProvider

CHAT_HISTORY_PREFIX = "/api/v1/chat_history"


class ChatStorageClient:
    """Клиент общего сервиса истории чатов.

    Контракт тот же, что у GenPlanner и GenBuilder: сервисный токен + ``X-User-Id``,
    сообщения многочастные (``parts``), чтобы в историю ложились и текст, и ссылки на слои.

    Тело ``create_chat`` — ``{title, scenario_id, project_id, metadata}``: сценарий и проект
    лежат на верхнем уровне, а не в ``metadata``, иначе чат не привяжется к сценарию
    и не найдётся в списке чатов проекта. ``metadata`` объявлена необнуляемой —
    ``null`` отдаётся как 422, поэтому всегда шлём хотя бы ``{}``.
    """

    def __init__(self, handler: AsyncJsonApiHandler, token_provider: ServiceTokenProvider):
        self._api = handler
        self._tokens = token_provider

    async def _headers(self, user_id: str | None) -> dict[str, str]:
        """`X-User-Id` обязателен при сервисном токене: под ним ChatStorage хранит историю."""
        headers = {"Authorization": f"Bearer {await self._tokens.get_token()}"}
        if user_id:
            headers["X-User-Id"] = str(user_id)
        else:
            logger.warning("Не удалось определить пользователя — ChatStorage отклонит запрос")
        return headers

    async def create_chat(
        self,
        title: str,
        user_id: str | None,
        scenario_id: int | str | None = None,
        project_id: int | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        response = await self._api.post(
            f"{CHAT_HISTORY_PREFIX}/create_chat",
            json_data={
                "title": title,
                "scenario_id": scenario_id,
                "project_id": project_id,
                "metadata": metadata or {},
            },
            headers=await self._headers(user_id),
        )
        return response["chat_id"]

    async def add_message(
        self,
        chat_id: str,
        role: str,
        parts: list[dict[str, Any]],
        user_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        response = await self._api.post(
            f"{CHAT_HISTORY_PREFIX}/{chat_id}/message",
            json_data={"role": role, "parts": parts, "metadata": metadata or {}},
            headers=await self._headers(user_id),
        )
        return (response or {}).get("message_id")

    async def get_chat(self, chat_id: str, user_id: str | None) -> dict[str, Any]:
        return await self._api.get(f"{CHAT_HISTORY_PREFIX}/{chat_id}", headers=await self._headers(user_id))

    @staticmethod
    def text_part(text: str) -> dict[str, Any]:
        return {"kind": "text", "payload": {"text": text}}

    @staticmethod
    def build_llm_history(chat: dict[str, Any]) -> list[dict[str, str]]:
        """Сплющивает многочастную историю в плоские сообщения для LLM."""
        history: list[dict[str, str]] = []
        for message in chat.get("messages", []):
            texts = [
                part.get("payload", {}).get("text", "")
                for part in message.get("parts", [])
                if part.get("kind") == "text"
            ]
            content = "\n".join(filter(None, texts)) or message.get("content") or ""
            if content:
                history.append({"role": message.get("role", "user"), "content": content})
        return history
