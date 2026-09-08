"""«С максимальной эффективностью, но в разумных пределах».

Дефолты GenBuilder (`density_scenario: min`, `floors_avg: 19`, `default_floor_group: extreme`)
дают ровно противоположное: минимальную плотность при нереалистичной этажности,
одинаковой для ИЖС и для высотки. Поэтому таргеты задаёт оркестратор (ADR-0001, D4):
верхняя граница плотности при нормативном потолке этажности по группе.

Внутри сервиса таргеты живут по зонам (`{зона: {параметр: значение}}`) — так их удобно
собирать, показывать пользователю и переопределять. GenBuilder же читает их наизнанку
(`{параметр: {зона: значение}}`), поэтому перед отправкой их разворачивает
`to_genbuilder_targets`. Обе формы — `dict[str, dict[str, Any]]`, так что перепутанную
GenBuilder примет молча и просто проигнорирует, вернувшись к своим дефолтам.
"""

from typing import Any

from app.common.constants.pipeline_constants import (
    GENBUILDER_TARGET_PARAMETERS,
    GENPLANNER_TO_GENBUILDER_ZONE,
    PROFILE_TARGETS,
    VOLUME_TARGET_BY_ZONE,
)

# Потолок этажности по группе — границы взяты из групп этажности самого GenBuilder
# (`BuildingType`: private 1–3, low 2–4, medium 5–8, high 9–16).
FLOORS_CAP_BY_GROUP: dict[str, int] = {"private": 2, "low": 4, "medium": 8, "high": 16}

# Чем застраивается зона, если её профиль не совпал с выбранным профилем территории.
FALLBACK_TARGETS_BY_ZONE: dict[str, dict[str, Any]] = {
    "residential": {"density_scenario": "mean", "floors_avg": 8, "default_floor_group": "medium", "residents": 3000},
    "business": {"density_scenario": "mean", "floors_avg": 8, "default_floor_group": "high", "coverage_area": 10000},
    "industrial": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "transport": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "special": {"density_scenario": "mean", "floors_avg": 2, "coverage_area": 10000},
    "unknown": {"density_scenario": "mean", "floors_avg": 5, "default_floor_group": "medium", "coverage_area": 10000},
}


def build_targets_by_zone(
    profile_id: int,
    zones_present: set[str],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Собирает таргеты по зонам — только для тех, которые реально есть в блоках.

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


def to_genbuilder_targets(targets_by_zone: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Разворачивает `{зона: {параметр: значение}}` в форму GenBuilder `{параметр: {зона: значение}}`."""
    wire: dict[str, dict[str, Any]] = {}
    for zone, target in targets_by_zone.items():
        for parameter, value in target.items():
            if parameter in GENBUILDER_TARGET_PARAMETERS:
                wire.setdefault(parameter, {})[zone] = value
    return wire


def zones_without_volume_target(targets_by_zone: dict[str, dict[str, Any]]) -> list[str]:
    """Зоны, для которых GenBuilder пропустит генерацию: нет ни `residents`, ни `coverage_area`."""
    missing = []
    for zone, target in sorted(targets_by_zone.items()):
        accepted = VOLUME_TARGET_BY_ZONE.get(zone, ("residents", "coverage_area"))
        if not any(float(target.get(key) or 0) > 0 for key in accepted):
            missing.append(zone)
    return missing


def _clamp_floors(target: dict[str, Any]) -> None:
    """«Разумные пределы»: этажность не выше потолка своей группы."""
    group = target.get("default_floor_group")
    cap = FLOORS_CAP_BY_GROUP.get(group) if isinstance(group, str) else None
    if cap is not None and isinstance(target.get("floors_avg"), (int, float)):
        target["floors_avg"] = min(int(target["floors_avg"]), cap)
