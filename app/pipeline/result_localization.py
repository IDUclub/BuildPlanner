"""Русские атрибуты зон и дорог для карты фронтенда.

Панель атрибутов слоя показывает свойства GeoJSON как есть, поэтому технические ключи
GenPlanner (`territory_zone`, `territory_zone_name`, `is_generated`) и английские виды зон
пользователь прочитал бы как отладочный вывод. Правила те же, что в чате GenPlanner
(там `app/chat/result_localization.py`), с двумя отличиями: `business` здесь «общественно-деловая»,
как у GenBuilder и в профилях, а ширина дороги остаётся — её видно на карте.

Переводится только то, что уходит фронтенду: события `zones`/`roads` и сохранённые слои.
GenBuilder, запись в Urban API и синхронный REST-ответ работают с исходными свойствами.
Застройка не переводится вовсе: подписи к ней фронтенд берёт у GenBuilder
(`GET /generate/properties_schema`), своя копия разошлась бы с ней.
"""

import re
from typing import Any, Callable

from app.common.constants.pipeline_constants import TERRITORY_ZONE_KIND_BY_ID, TERRITORY_ZONE_KIND_NAMES

ZONE_LABEL = "Территориальная зона"
GENERATED_LABEL = "Сгенерирована"
SOURCE_ZONE_ID_LABEL = "Идентификатор исходной зоны"

ROAD_NAME_LABEL = "Название"
ROAD_ADDRESS_LABEL = "Адрес"
ROAD_WIDTH_LABEL = "Ширина, м"

ROAD_LEVEL_KEY = "road_lvl"
ROAD_CLASS_KEY = "road_class"
ROAD_WIDTH_KEY = "roads_width"
PHYSICAL_OBJECT_TYPE_KEY = "physical_object_type_id"

ROAD_CLASS_HIGHWAY = "highway"
ROAD_CLASS_STREET = "street"
ROAD_CLASS_EXISTING = "existing"

_ROAD_CLASS_BY_LEVEL_PREFIX: tuple[tuple[str, str], ...] = (
    ("regulated highway", ROAD_CLASS_HIGHWAY),
    ("local road", ROAD_CLASS_STREET),
    ("user_roads", ROAD_CLASS_EXISTING),
)

UNKNOWN_ZONE_NAME = "не определена"
_YES = "Да"
_NO = "Нет"


def _zone_kind_by_id(zone_id: Any) -> str | None:
    if isinstance(zone_id, bool):
        return None
    try:
        return TERRITORY_ZONE_KIND_BY_ID.get(int(zone_id))
    except (TypeError, ValueError):
        return None


def zone_name(properties: dict[str, Any]) -> str:
    """Русское имя зоны: по виду из `territory_zone_name`, иначе по id из `territory_zone`.

    Сгенерированные зоны несут вид всегда, а зоны, подмешанные из существующего зонирования,
    могут нести только id.
    """
    kind = properties.get("territory_zone_name")
    if isinstance(kind, str) and kind.strip().lower() in TERRITORY_ZONE_KIND_NAMES:
        return TERRITORY_ZONE_KIND_NAMES[kind.strip().lower()]
    kind_by_id = _zone_kind_by_id(properties.get("territory_zone"))
    return TERRITORY_ZONE_KIND_NAMES[kind_by_id] if kind_by_id else UNKNOWN_ZONE_NAME


def localize_zone_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Белый список, а не переименование: зоны из Urban API тянут за собой всю запись
    (`year`, `source`, `created_at`…), которую в панели атрибутов не показать осмысленно.
    """
    localized: dict[str, Any] = {ZONE_LABEL: zone_name(properties)}
    is_generated = properties.get("is_generated")
    if is_generated is not None:
        localized[GENERATED_LABEL] = _YES if is_generated else _NO
    source_zone_id = properties.get("functional_zone_id")
    if source_zone_id is not None:
        localized[SOURCE_ZONE_ID_LABEL] = source_zone_id
    return localized


def road_class(road_level: Any) -> str | None:
    """Сводит `road_lvl` к трём значениям для легенды.

    `local road, level N` — не закрытый набор: глубина N зависит от площади зоны.
    Незнакомый уровень класса не получает — выдуманная категория раскрасилась бы как настоящая.
    """
    if not isinstance(road_level, str):
        return None
    normalized = re.sub(r"\s+", " ", road_level.strip().lower())
    for prefix, value in _ROAD_CLASS_BY_LEVEL_PREFIX:
        if normalized.startswith(prefix):
            return value
    return None


def localize_road_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Читаемые атрибуты плюс те, по которым красится слой.

    `physical_object_type_id`, `road_lvl` и `road_class` остаются машинными: перевести то,
    от чего зависит стиль, значило бы привязать цвета слоя к тексту подписи.
    """
    localized: dict[str, Any] = {}
    for key, label in (("name", ROAD_NAME_LABEL), ("address", ROAD_ADDRESS_LABEL), (ROAD_WIDTH_KEY, ROAD_WIDTH_LABEL)):
        value = properties.get(key)
        if value is not None:
            localized[label] = value

    object_type_id = properties.get(PHYSICAL_OBJECT_TYPE_KEY)
    if object_type_id is not None:
        localized[PHYSICAL_OBJECT_TYPE_KEY] = object_type_id

    road_level = properties.get(ROAD_LEVEL_KEY)
    if road_level is not None:
        localized[ROAD_LEVEL_KEY] = road_level
    level_class = road_class(road_level)
    if level_class is not None:
        localized[ROAD_CLASS_KEY] = level_class
    return localized


def _localize_collection(collection: Any, localize_properties: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
    """Переводит свойства каждого объекта; геометрия и сама коллекция не трогаются, исходник не меняется."""
    if not isinstance(collection, dict) or not isinstance(collection.get("features"), list):
        return collection
    features = [
        (
            {**feature, "properties": localize_properties(feature.get("properties") or {})}
            if isinstance(feature, dict)
            else feature
        )
        for feature in collection["features"]
    ]
    return {**collection, "features": features}


def localize_zones(collection: Any) -> Any:
    return _localize_collection(collection, localize_zone_properties)


def localize_roads(collection: Any) -> Any:
    return _localize_collection(collection, localize_road_properties)
