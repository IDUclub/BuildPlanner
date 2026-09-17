"""Словарь SSE-событий.

Совпадает со словарём GenPlanner и GenBuilder — фронтенд, умеющий читать их потоки,
читает и этот. Своих событий восемь: `indicators`, `territory_indicators`, `profile_selected`,
`scenario_published`, `sirtep_schedule`, `sirtep_provision`, `scores` и `master_plan_summary`
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
STAGE_PUBLISH = "publish_scenario"
STAGE_SIRTEP = "sirtep"
STAGE_SCORES = "await_scores"
STAGE_STORE_LAYER = "store_layer"

STAGE_TITLES: dict[str, str] = {
    STAGE_FETCH_INDICATORS: "Читаю показатели сценария",
    STAGE_SELECT_PROFILE: "Выбираю профиль застройки",
    STAGE_GENPLANNER: "Генерирую территориальные зоны",
    STAGE_MAP_ZONES: "Готовлю блоки для застройки",
    STAGE_GENBUILDER: "Расставляю застройку",
    STAGE_ASSEMBLE: "Собираю результат",
    STAGE_PUBLISH: "Сохраняю сценарий для расчёта оценок",
    STAGE_SIRTEP: "Считаю очерёдность строительства",
    STAGE_SCORES: "Жду расчёт оценок",
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


def layer(kind: str, descriptor: dict[str, Any], summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Типизированное событие слоя; GeoJSON берётся фронтендом по ``content.url``."""
    event: dict[str, Any] = {"type": kind, "content": descriptor}
    if summary is not None:
        event["summary"] = summary
    return event


def zones(content: dict[str, Any], source: str = "genplanner") -> dict[str, Any]:
    """Резервный inline-формат, когда хранилище слоёв не настроено."""
    return {"type": "zones", "source": source, "content": content}


def roads(content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "roads", "content": content}


def result(content: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    return {"type": "result", "content": content, "summary": summary}


def file(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Общее PZZ-совместимое событие слоя."""
    return {"type": "file", "content": descriptor}


def scenario_published(published: dict[str, Any]) -> dict[str, Any]:
    """Сценарий записан в Urban API и о нём объявлено в брокер.

    Отсюда начинается счёт оценок: сервисы, подписанные на `urban.events`,
    берут сценарий по этому `scenario_id`.
    """
    return {"type": "scenario_published", **published}


def sirtep_schedule(content: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Очередь строительства целиком плюс её сводка.

    В `content` лежит ответ SIRTEP как есть: в нём периоды по каждому дому и сервису,
    по ним фронтенд раскрашивает карту. В `summary` — то же самое для чтения.
    """
    return {"type": "sirtep_schedule", "content": content, "summary": summary}


def sirtep_provision(content: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """ТЭПы: как растёт обеспеченность по ходу стройки и что в горизонт не влезло."""
    return {"type": "sirtep_provision", "content": content, "summary": summary}


def scores(content: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Оценки, посчитанные сторонними сервисами по опубликованному сценарию.

    В `content` — значения индикаторов как есть (для карты/таблицы), в `summary` — то же
    для чтения. «Слушать брокер» напрямую нельзя, поэтому ждём появления свежих значений
    у сценария; частичный набор по таймауту — тоже результат (см. `ScoreWatcher`).
    """
    return {"type": "scores", "content": content, "summary": summary}


def master_plan_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Итог прогона одним событием: застройка, публикация, очередь, обеспеченность."""
    return {"type": "master_plan_summary", **summary}


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
