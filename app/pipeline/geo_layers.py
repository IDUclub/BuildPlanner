"""Слои прогона, которые должны пережить перезагрузку чата.

В потоке зоны, дороги и застройка приходят целиком, но в историю ChatStorage кладутся
только ссылки. Поэтому каждый слой после отправки пишется в хранилище под id прогона,
а в поток и в историю уходит дескриптор — куда за ним сходить.

`url` — долговечный относительный путь: байты идут потоком через этот сервис, потому что
MinIO из внешней сети недоступен. Адреса сервиса в ссылке нет: фронтенд сам подставляет
базу по `source_service` — внутренний адрес пода, запомненный в истории, снаружи не открылся бы.
`download_url` всегда пуст — прямых ссылок на MinIO нет.
Форма дескриптора совпадает с PZZ: фронтенд получает его в SSE и запрашивает слой отдельно.
"""

import asyncio
import re
from typing import Any

from app.common.object_storage.object_storage import ObjectStorage

SOURCE_SERVICE = "buildplanner"
MIME_TYPE = "application/geo+json"
FILES_PATH = "/buildplanner/files"

SLOT_ZONES = "zones"
SLOT_ROADS = "roads"
SLOT_BUILDINGS = "buildings"

# Слот совпадает с событием потока, в котором слой пришёл целиком, кроме застройки: она едет в `result`.
SLOT_TITLES: dict[str, str] = {
    SLOT_ZONES: "Территориальные зоны",
    SLOT_ROADS: "Дороги",
    SLOT_BUILDINGS: "Сгенерированная застройка",
}

RESULT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def object_key(result_id: str, slot: str) -> str:
    """Ключ выводится из id прогона и слота — ручке файла не нужен никакой поиск."""
    if slot not in SLOT_TITLES:
        raise ValueError(f"Неизвестный слот слоя: {slot!r}")
    if not RESULT_ID_RE.match(result_id):
        raise ValueError(f"Некорректный id прогона: {result_id!r}")
    return f"{result_id}/{slot}.geojson"


def layer_descriptor(slot: str, result_id: str) -> dict[str, Any]:
    return {
        "name": slot,
        "title": SLOT_TITLES[slot],
        "role": "result",
        "url": f"{FILES_PATH}/{slot}/{result_id}",
        "download_url": None,
        "filename": f"{slot}.geojson",
        "mime_type": MIME_TYPE,
        "source_service": SOURCE_SERVICE,
    }


class LayerStore:
    """Пишет слой в хранилище и возвращает его дескриптор."""

    def __init__(self, storage: ObjectStorage):
        self.storage = storage

    async def store(
        self,
        slot: str,
        result_id: str,
        content: dict[str, Any],
        _request_base_url: str | None = None,
    ) -> dict[str, Any]:
        # Адрес запроса больше не нужен — ссылка относительная; параметр оставлен для вызывающих.
        # Клиент MinIO синхронный: в потоке событий его нельзя звать напрямую.
        key = object_key(result_id, slot)
        await asyncio.to_thread(self.storage.put_json, content, key)
        return layer_descriptor(slot, result_id)
