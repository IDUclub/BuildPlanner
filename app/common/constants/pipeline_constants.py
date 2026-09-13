"""Мосты между тремя чужими системами идентификаторов.

Здесь и только здесь живут три перевода:

1. индикатор Urban API -> `profile_id` GenPlanner;
2. территориальная зона GenPlanner -> зона + группа этажности GenBuilder;
3. профиль -> целевые параметры застройки.

Большинство ошибок пайплайна сводится к рассинхрону одной из этих таблиц —
поэтому они собраны в одном модуле и покрыты тестом на полноту (`tests/test_constants.py`).
См. ADR-0001, разделы D2 и D4.
"""

from typing import Final

# --- 1. Индикаторы --------------------------------------------------------------------------

# Семейство 269 «Потенциал развития застройки»: единая безразмерная шкала,
# значения сравнимы между собой напрямую.
# Семейство 284 («баллов»: 286 бизнес-кластер, 287 промзона, 288 логистика) в выборе НЕ участвует.
INDICATOR_TO_PROFILE: Final[dict[int, int]] = {
    271: 10,  # Потенциал развития жилой застройки типа ИЖС
    272: 11,  # Потенциал развития малоэтажной жилой застройки
    273: 12,  # Потенциал развития среднеэтажной жилой застройки
    274: 13,  # Потенциал развития многоэтажной жилой застройки
    275: 7,  # Потенциал развития застройки общественно-деловой зоны
    276: 2,  # Потенциал развития застройки рекреационной зоны
    277: 3,  # Потенциал развития застройки зоны специального назначения
    278: 4,  # Потенциал развития застройки промышленной зоны
    279: 5,  # Потенциал развития застройки сельскохозяйственной зоны
    280: 6,  # Потенциал развития застройки транспортной зоны
}

SELECTION_INDICATOR_IDS: Final[tuple[int, ...]] = tuple(sorted(INDICATOR_TO_PROFILE))

INDICATOR_NAMES: Final[dict[int, str]] = {
    271: "ИЖС",
    272: "малоэтажная жилая",
    273: "среднеэтажная жилая",
    274: "многоэтажная жилая",
    275: "общественно-деловая",
    276: "рекреационная",
    277: "специального назначения",
    278: "промышленная",
    279: "сельскохозяйственная",
    280: "транспортная",
}

# --- 2. Профили GenPlanner ------------------------------------------------------------------

# Это же пространство идентификаторов, что и `functional_zone_type_id` Urban API:
# 1 residential, 2 recreation, 7 business, 13 residential_multistorey и так далее.
# Поэтому при записи сценария зона не переводится, а проверяется по справочнику стенда
# (`GET /api/v1/functional_zones_types`) — см. `UrbanScenarioWriter.add_functional_zones`.
PROFILE_NAMES: Final[dict[int, str]] = {
    1: "жилая",
    2: "рекреационная",
    3: "специального назначения",
    4: "промышленная",
    5: "сельскохозяйственная",
    6: "транспортная",
    7: "общественно-деловая",
    8: "базовая",
    10: "жилая (ИЖС)",
    11: "жилая малоэтажная",
    12: "жилая среднеэтажная",
    13: "жилая многоэтажная",
}

# Обратный перевод для случая, когда GenPlanner вернул зону только человекочитаемым именем.
ZONE_NAME_TO_PROFILE: Final[dict[str, int]] = {
    "жилая": 1,
    "рекреационная": 2,
    "специального назначения": 3,
    "промышленная": 4,
    "сельскохозяйственная": 5,
    "транспортная": 6,
    "деловая": 7,
    "общественно-деловая": 7,
    "базовая": 8,
}

# --- 3. Таксономия GenBuilder ---------------------------------------------------------------

# Зона GenBuilder + группа этажности. `None` означает, что зона не застраивается:
# в таксономии GenBuilder нет ни рекреации, ни сельского хозяйства, такие блоки он исключает.
GENPLANNER_TO_GENBUILDER_ZONE: Final[dict[int, tuple[str, str | None] | None]] = {
    1: ("residential", "medium"),
    2: None,  # рекреационная — не застраивается
    3: ("special", None),
    4: ("industrial", None),
    5: None,  # сельскохозяйственная — не застраивается
    6: ("transport", None),
    7: ("business", "high"),
    8: ("unknown", "medium"),
    10: ("residential", "private"),
    11: ("residential", "low"),
    12: ("residential", "medium"),
    13: ("residential", "high"),
}

NON_BUILDABLE_PROFILES: Final[frozenset[int]] = frozenset(
    profile_id for profile_id, mapping in GENPLANNER_TO_GENBUILDER_ZONE.items() if mapping is None
)

# Сервисы (школы, сады, поликлиники) GenBuilder ставит только в жилых кварталах.
RESIDENTIAL_ZONE: Final[str] = "residential"

# Зона GenBuilder -> тип физобъекта Urban API, под которым здание записывается в сценарий.
# Не id, а имя: id справочника у каждого стенда свои, а имена совпадают.
# GenBuilder ставит и жильё, и общественно-деловую застройку — свести их в один тип
# означало бы соврать о назначении половины зданий.
BUILDING_TYPE_NAME_BY_ZONE: Final[dict[str, str]] = {"residential": "Жилой дом"}
DEFAULT_BUILDING_TYPE_NAME: Final[str] = "Нежилое здание"

# --- 4. Политика застройки ------------------------------------------------------------------

# «С максимальной эффективностью, но в разумных пределах»: верхняя граница плотности
# при нормативном потолке этажности по группе. Дефолты GenBuilder
# (`density_scenario: min`, `floors_avg: 19`, `default_floor_group: extreme`) не используются.
#
# Обязательный минимум: у каждого профиля должна быть цель объёма — `residents` для жилья
# и `coverage_area` для нежилья. Без неё GenBuilder пропускает соответствующую ветку
# генерации целиком (`la_target <= 0` / `coverage_target <= 0`) и возвращает пустой результат.
#
# Обе цели — абсолютные величины на всю территорию, поэтому в таблицах они заданы удельно:
# `residents_per_ha` (чел/га) и `coverage_ratio` (доля площади блоков зоны под застройку).
# В абсолютные значения их переводит `targets_policy` по фактической площади блоков.
PROFILE_TARGETS: Final[dict[int, dict[str, object]]] = {
    1: {"density_scenario": "max", "floors_avg": 8, "default_floor_group": "medium", "residents_per_ha": 250},
    3: {"density_scenario": "mean", "floors_avg": 2, "coverage_ratio": 0.20},
    4: {"density_scenario": "max", "floors_avg": 2, "coverage_ratio": 0.45},
    6: {"density_scenario": "mean", "floors_avg": 2, "coverage_ratio": 0.25},
    7: {"density_scenario": "max", "floors_avg": 12, "default_floor_group": "high", "coverage_ratio": 0.35},
    8: {"density_scenario": "mean", "floors_avg": 5, "default_floor_group": "medium", "coverage_ratio": 0.25},
    10: {"density_scenario": "max", "floors_avg": 2, "default_floor_group": "private", "residents_per_ha": 45},
    11: {"density_scenario": "max", "floors_avg": 4, "default_floor_group": "low", "residents_per_ha": 130},
    12: {"density_scenario": "max", "floors_avg": 8, "default_floor_group": "medium", "residents_per_ha": 250},
    13: {"density_scenario": "max", "floors_avg": 16, "default_floor_group": "high", "residents_per_ha": 420},
}

# Удельные ключи оркестратора: в тело GenBuilder они не уходят, из них считаются
# `residents` и `coverage_area`.
RATE_TO_VOLUME_TARGET: Final[dict[str, str]] = {
    "residents_per_ha": "residents",
    "coverage_ratio": "coverage_area",
}

# «В разумных пределах»: верхние границы удельных величин.
# Плотность — по потолку своей группы этажности (СП 42.13330, порядок величин),
# застроенность — доля, выше которой квартал перестаёт быть кварталом.
RESIDENTS_PER_HECTARE_CAP: Final[dict[str, int]] = {"private": 60, "low": 180, "medium": 320, "high": 450}
COVERAGE_RATIO_CAP: Final[float] = 0.60

# Как GenBuilder читает `targets_by_zone`: внешний ключ — параметр, внутренний — зона.
GENBUILDER_TARGET_PARAMETERS: Final[tuple[str, ...]] = (
    "residents",
    "coverage_area",
    "floors_avg",
    "density_scenario",
    "default_floor_group",
)

# `density_scenario` вне этого набора роняет генерацию: `Unknown FAR scenario`.
DENSITY_SCENARIOS: Final[frozenset[str]] = frozenset({"min", "mean", "max"})

# Какая цель объёма включает генерацию для группы зон GenBuilder.
# Жильё считает `residents` -> жилую площадь; промзона/транспорт/спецназначение — `coverage_area`;
# деловая и «базовая» идут в смешанную ветку, ей хватит любой из двух.
VOLUME_TARGET_BY_ZONE: Final[dict[str, tuple[str, ...]]] = {
    "residential": ("residents",),
    "business": ("residents", "coverage_area"),
    "unknown": ("residents", "coverage_area"),
    "industrial": ("coverage_area",),
    "transport": ("coverage_area",),
    "special": ("coverage_area",),
}

# --- 5. Витрина показателей сценария --------------------------------------------------------

# Названия и единицы Urban API отдаёт сам (`indicator.name_full`, `measurement_unit`),
# поэтому своей таблицы показателей здесь нет — только порядок и заголовки разделов.

# Короткая сводка «паспорт территории»: с чего планировщик начинает смотреть на проект.
# Порядок важен — в таком виде показатели уходят в таблицу ответа.
HIGHLIGHT_INDICATOR_IDS: Final[tuple[int, ...]] = (
    1,  # Численность населения
    37,  # Плотность населения
    4,  # Площадь территории
    16,  # Степень урбанизации территории
    24,  # Процент земель населенных пунктов
    43,  # Средний возраст населения
    60,  # Плотность улично-дорожной сети
    25,  # Средняя оценка индекса качества городской среды
)

# Группы приходят слагами (`/api/v1/indicators_groups`), в том числе с опечатками вроде
# `demogrphy`. Незнакомый слаг показывается как есть — новая группа не ломает вывод.
INDICATOR_GROUP_TITLES: Final[dict[str, str]] = {
    "demogrphy": "Демография",
    "settelment": "Расселение",
    "economy": "Экономика",
    "transport": "Транспорт",
    "engineering": "Инженерная инфраструктура",
    "nature": "Природа и экология",
    "soc_edu": "Образование",
    "soc_health": "Здравоохранение",
    "soc_safe": "Безопасность",
    "soc_help": "Социальная помощь",
    "culture": "Культура и досуг",
    "sport": "Спорт",
    "tourism": "Туризм и общепит",
    "service": "Услуги и сервис",
    "base": "Базовое обслуживание",
    "profiles": "Профиль территории",
    "regional": "Региональные",
    "common": "Общие",
    "mandatory": "Обязательные",
}

# Один индикатор лежит сразу в нескольких группах (например, 12 — и в `common`, и в `regional`,
# и в `settelment`). В таблице он должен встретиться один раз, поэтому раздел выбирается
# по этому порядку: тематические группы вперёд, сборные — в конец.
INDICATOR_GROUP_ORDER: Final[tuple[str, ...]] = (
    "demogrphy",
    "settelment",
    "economy",
    "transport",
    "engineering",
    "nature",
    "soc_edu",
    "soc_health",
    "soc_safe",
    "soc_help",
    "culture",
    "sport",
    "tourism",
    "service",
    "base",
    "profiles",
    "regional",
    "common",
    "mandatory",
)

# Показатели вне групп — в том числе семейство 269, по которому выбирается профиль.
UNGROUPED_SECTION: Final[str] = "other"
UNGROUPED_SECTION_TITLE: Final[str] = "Прочие показатели"

# Ключи свойств, под которыми GenPlanner отдаёт территориальную зону в feature.
ZONE_ID_PROPERTY_KEYS: Final[tuple[str, ...]] = ("territory_zone", "zone_id", "profile_id")
ZONE_NAME_PROPERTY_KEYS: Final[tuple[str, ...]] = ("territory_zone_name", "zone_name", "Территориальная зона")
