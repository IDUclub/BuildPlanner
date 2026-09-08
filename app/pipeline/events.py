"""Словарь SSE-событий.

Совпадает со словарём GenPlanner и GenBuilder — фронтенд, умеющий читать их потоки,
читает и этот. Своих событий три: `indicators`, `territory_indicators` и `profile_selected`
(ADR-0001, D5).
Каждое событие — словарь с ключом `type`, который контроллер превращает в имя SSE-события.
"""

from typing import Any

STAGE_FETCH_INDICATORS = "fetch_indicators"
STAGE_SELECT_PROFILE = "select_profile"
STAGE_GENPLANNER = "genplanner"
STAGE_MAP_ZONES = "map_zones"
STAGE_GENBUILDER = "genbuilder"
STAGE_ASSEMBLE = "assemble"

STAGE_TITLES: dict[str, str] = {
    STAGE_FETCH_INDICATORS: "Читаю показатели сценария",
    STAGE_SELECT_PROFILE: "Выбираю профиль застройки",
    STAGE_GENPLANNER: "Генерирую территориальные зоны",
    STAGE_MAP_ZONES: "Готовлю блоки для застройки",
    STAGE_GENBUILDER: "Расставляю застройку",
    STAGE_ASSEMBLE: "Собираю результат",
}


def progress(stage: str, content: str | None = None) -> dict[str, Any]:
    return {"type": "progress", "stage": stage, "content": content or STAGE_TITLES.get(stage, stage)}


def indicators(values: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "indicators", "values": values}


def territory_indicators(overview: dict[str, Any]) -> dict[str, Any]:
    """Показатели проекта целиком: короткая сводка и полная таблица по разделам.

    Отдельное событие, а не часть `indicators`: там — только те десять, по которым
    выбирается профиль, и сравнивать их с численностью населения нельзя.
    """
    return {"type": "territory_indicators", **overview}


def profile_selected(selection: dict[str, Any]) -> dict[str, Any]:
    return {"type": "profile_selected", **selection}


def zones(content: dict[str, Any], source: str = "genplanner") -> dict[str, Any]:
    return {"type": "zones", "source": source, "content": content}


def roads(content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "roads", "content": content}


def result(content: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    return {"type": "result", "content": content, "summary": summary}


def token(content: str) -> dict[str, Any]:
    return {"type": "token", "content": content}


def chat_created(chat_id: str, title: str) -> dict[str, Any]:
    return {"type": "chat_created", "chat_id": chat_id, "title": title}


def warning(stage: str, detail: str, message: str | None = None) -> dict[str, Any]:
    return {"type": "warning", "stage": stage, "detail": detail, "message": message}


def error(stage: str, detail: str) -> dict[str, Any]:
    return {"type": "error", "stage": stage, "detail": detail}


def done(chat_id: str | None = None, assistant_message_id: str | None = None) -> dict[str, Any]:
    return {"type": "done", "chat_id": chat_id, "assistant_message_id": assistant_message_id}
