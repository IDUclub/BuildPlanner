"""Слои прогона, которые должны пережить перезагрузку чата.

В потоке зоны, дороги и застройка приходят целиком, но в историю ChatStorage кладутся
только ссылки. Поэтому каждый слой после отправки пишется в хранилище под id прогона,
а в поток и в историю уходит дескриптор — куда за ним сходить.

`download_url` всегда `None`: хранилище в закрытой сети, байты отдаёт наша ручка
`/buildplanner/files/{slot}/{result_id}`. Форма дескриптора та же, что у GenBuilder.
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


def durable_url(path: str, public_base_url: str | None, request_base_url: str | None) -> str:
    """Абсолютная ссылка, если известен `PUBLIC_BASE_URL`, — именно она нужна истории чата.

    Без него адрес берётся из входящего запроса, а в крайнем случае остаётся относительным:
    живому потоку этого хватает, истории, открытой с другого origin, — нет.
    """
    base = public_base_url or request_base_url
    return f"{base.rstrip('/')}{path}" if base else path


def layer_descriptor(
    slot: str,
    result_id: str,
    public_base_url: str | None = None,
    request_base_url: str | None = None,
) -> dict[str, Any]:
    return {
        "name": slot,
        "title": SLOT_TITLES[slot],
        "role": "result",
        "url": durable_url(f"{FILES_PATH}/{slot}/{result_id}", public_base_url, request_base_url),
        "download_url": None,
        "filename": f"{slot}.geojson",
        "mime_type": MIME_TYPE,
        "source_service": SOURCE_SERVICE,
    }


class LayerStore:
    """Пишет слой в хранилище и возвращает его дескриптор."""

    def __init__(self, storage: ObjectStorage, public_base_url: str | None = None):
        self.storage = storage
        self._public_base_url = public_base_url or None

    async def store(
        self,
        slot: str,
        result_id: str,
        content: dict[str, Any],
        request_base_url: str | None = None,
    ) -> dict[str, Any]:
        # Клиент MinIO синхронный: в потоке событий его нельзя звать напрямую.
        await asyncio.to_thread(self.storage.put_json, content, object_key(result_id, slot))
        return layer_descriptor(slot, result_id, self._public_base_url, request_base_url)
