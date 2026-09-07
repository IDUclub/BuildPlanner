"""«С максимальной эффективностью, но в разумных пределах».

Дефолты GenBuilder (`density_scenario: min`, `floors_avg: 19`, `default_floor_group: extreme`)
дают ровно противоположное: минимальную плотность при нереалистичной этажности,
одинаковой для ИЖС и для высотки. Поэтому таргеты задаёт оркестратор (ADR-0001, D4):
верхняя граница плотности при нормативном потолке этажности по группе.
"""

from typing import Any

from app.common.constants.pipeline_constants import GENPLANNER_TO_GENBUILDER_ZONE, PROFILE_TARGETS

# Потолок этажности по группе — границы взяты из групп этажности самого GenBuilder.
FLOORS_CAP_BY_GROUP: dict[str, int] = {"private": 2, "low": 4, "medium": 8, "high": 16}

# Чем застраивается зона, если её профиль не совпал с выбранным профилем территории.
FALLBACK_TARGETS_BY_ZONE: dict[str, dict[str, Any]] = {
    "residential": {"density_scenario": "mean", "floors_avg": 8, "default_floor_group": "medium"},
    "business": {"density_scenario": "mean", "floors_avg": 8, "default_floor_group": "high", "coverage_area": 10000},
    "industrial": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "transport": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "special": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "unknown": {"density_scenario": "mean", "floors_avg": 5, "default_floor_group": "medium"},
}


def build_targets_by_zone(
    profile_id: int,
    zones_present: set[str],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Собирает `targets_by_zone` только для тех зон, которые реально есть в блоках.

    Зона выбранного профиля застраивается по максимуму, остальные — умеренно:
    профиль задаёт баланс территории, а не единственный тип застройки.
    """
    targets: dict[str, dict[str, Any]] = {}
    profile_mapping = GENPLANNER_TO_GENBUILDER_ZONE.get(profile_id)
    profile_zone = profile_mapping[0] if profile_mapping else None

    for zone in sorted(zones_present):
        if zone == profile_zone and profile_id in PROFILE_TARGETS:
            targets[zone] = dict(PROFILE_TARGETS[profile_id])
        else:
            targets[zone] = dict(FALLBACK_TARGETS_BY_ZONE.get(zone, FALLBACK_TARGETS_BY_ZONE["unknown"]))
        _clamp_floors(targets[zone])

    for zone, override in (overrides or {}).items():
        if zone in targets:
            targets[zone].update(override)
            _clamp_floors(targets[zone])

    return targets


def _clamp_floors(target: dict[str, Any]) -> None:
    """«Разумные пределы»: этажность не выше потолка своей группы."""
    group = target.get("default_floor_group")
    cap = FLOORS_CAP_BY_GROUP.get(group) if isinstance(group, str) else None
    if cap is not None and isinstance(target.get("floors_avg"), (int, float)):
        target["floors_avg"] = min(int(target["floors_avg"]), cap)
