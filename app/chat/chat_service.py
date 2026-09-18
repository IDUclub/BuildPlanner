"""Чат поверх пайплайна.

Порядок событий повторяет GenPlanner: сначала (возможный) `warning` о загрузке истории,
затем `chat_created`, затем `token`-и ответа модели, затем события самого прогона,
и всегда последним — `done`. Сбои ChatStorage и vLLM не рвут поток.
"""

from typing import Any, AsyncIterator

from loguru import logger

from app.chat.agent.prompts import DRAFT_SCHEMA, SYSTEM_PROMPT, TITLE_HINT
from app.chat.chat_title import normalize_title
from app.chat.dto.chat_dto import ChatTurnDTO
from app.common.auth.user_identity import extract_user_id
from app.common.chat_storage.chat_storage_client import ChatStorageClient
from app.common.llm.vllm_chat_client import VLLMChatClient, VLLMChatError
from app.pipeline import events
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.indicators_view import highlights_table
from app.pipeline.master_plan import summary_text
from app.pipeline.pipeline_service import PipelineService

TOKEN_CHUNK = 24
DEFAULT_REPLY = "Запускаю подбор профиля и генерацию по сценарию."


class ChatService:
    def __init__(
        self,
        pipeline: PipelineService,
        llm_client: VLLMChatClient | None,
        chat_storage: ChatStorageClient | None,
    ):
        self._pipeline = pipeline
        self._llm = llm_client
        self._storage = chat_storage

    async def stream(
        self,
        scenario_id: int,
        turn: ChatTurnDTO,
        token: str,
        base_url: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        user_id = extract_user_id(token)
        chat_id = turn.chat_id
        history: list[dict[str, str]] = []

        if self._storage and chat_id:
            try:
                history = ChatStorageClient.build_llm_history(await self._storage.get_chat(chat_id, user_id))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Не удалось загрузить историю чата {}: {}", chat_id, exc)
                yield events.warning("load_history", str(exc)[:500], "История чата недоступна, продолжаю без неё.")

        draft = await self._ask_model(turn.user_query, history)

        if self._storage and not chat_id:
            title = normalize_title(draft.get("title"), turn.user_query, scenario_id)
            try:
                chat_id = await self._storage.create_chat(
                    title,
                    user_id,
                    scenario_id=scenario_id,
                    metadata={"service": "buildplanner"},
                )
                yield events.chat_created(chat_id, title)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Не удалось создать чат: {}", exc)
                yield events.warning("create_chat", str(exc)[:500], "Чат не сохранён, ответ придёт как есть.")

        reply = draft.get("reply") or DEFAULT_REPLY
        for start in range(0, len(reply), TOKEN_CHUNK):
            yield events.token(reply[start : start + TOKEN_CHUNK])

        await self._persist(chat_id, "user", turn.user_query, user_id)

        if draft.get("action") != "run_pipeline":
            await self._persist(chat_id, "assistant", reply, user_id)
            yield events.done(chat_id)
            return

        options = self._merge_options(turn.options, draft.get("patch") or {})
        summary_lines: list[str] = [reply]
        file_parts: list[dict[str, Any]] = []

        async for event in self._pipeline.stream(scenario_id, token, options, base_url=base_url):
            if event["type"] == "file" and (descriptor := event.get("content") or event).get("url"):
                # Сами слои в историю не влезут — кладём ссылки, по ним фронтенд перерисует карту.
                file_parts.append(ChatStorageClient.file_part(descriptor))
            summary_lines.append(_summary_line(event))
            yield event

        message_id = await self._persist(
            chat_id,
            "assistant",
            "\n".join(filter(None, summary_lines)),
            user_id,
            # Ручные переопределения должны пережить перезагрузку чата:
            # из текста реплики их потом не восстановить надёжно.
            metadata={"options": options.model_dump(exclude_none=True)},
            extra_parts=file_parts,
        )
        yield events.done(chat_id, message_id)

    # ------------------------------------------------------------------ внутренности

    async def _ask_model(self, user_query: str, history: list[dict[str, str]]) -> dict[str, Any]:
        """Без vLLM сервис остаётся рабочим: считаем, что пользователь просит прогон."""
        if self._llm is None:
            return {"action": "run_pipeline", "reply": DEFAULT_REPLY}

        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{TITLE_HINT}"},
            *history,
            {"role": "user", "content": user_query},
        ]
        try:
            return await self._llm.complete_json(messages, DRAFT_SCHEMA)
        except VLLMChatError as exc:
            logger.warning("vLLM недоступен, работаю без него: {}", exc)
            return {"action": "run_pipeline", "reply": DEFAULT_REPLY}

    @staticmethod
    def _merge_options(base: PipelineOptionsDTO | None, patch: dict[str, Any]) -> PipelineOptionsDTO:
        merged = (base or PipelineOptionsDTO()).model_dump()
        for key, value in patch.items():
            if value is not None and key in merged:
                merged[key] = value
        return PipelineOptionsDTO(**merged)

    async def _persist(
        self,
        chat_id: str | None,
        role: str,
        text: str,
        user_id: str | None,
        metadata: dict[str, Any] | None = None,
        extra_parts: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if not (self._storage and chat_id and text):
            return None
        parts = [ChatStorageClient.text_part(text), *(extra_parts or [])]
        try:
            return await self._storage.add_message(chat_id, role, parts, user_id, metadata=metadata)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Не удалось сохранить сообщение в чат {}: {}", chat_id, exc)
            return None


def _summary_line(event: dict[str, Any]) -> str:
    """Что из события попадает в текст сохранённого ответа; пустая строка — ничего.

    Порядок строк — порядок событий, поэтому справка по прогону оказывается последней.
    """
    kind = event["type"]
    if kind == "territory_indicators":
        # В текст ответа идёт только короткая сводка: полная таблица уехала событием,
        # её рисует фронтенд, и дублировать её в сообщение незачем.
        table = highlights_table(event.get("highlights") or [])
        return f"Показатели территории:\n{table}" if table else ""
    if kind == "profile_selected":
        return str(event.get("reason", ""))
    if kind == "scenario_published":
        return (
            f"Результат сохранён сценарием {event.get('scenario_id')} "
            f"в проекте {event.get('project_id')} — {_scoring_status(event)}."
        )
    if kind == "master_plan_summary":
        return summary_text(event)
    if kind == "warning":
        return str(event.get("message") or "")
    return ""


def _scoring_status(published: dict[str, Any]) -> str:
    if published.get("notified"):
        return "по нему считаются оценки"
    if published.get("notified_events"):
        return "расчёт оценок запущен частично"
    return "расчёт оценок не запущен"
