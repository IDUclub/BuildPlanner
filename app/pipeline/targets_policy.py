"""«С максимальной эффективностью, но в разумных пределах».

Дефолты GenBuilder (`density_scenario: min`, `floors_avg: 19`, `default_floor_group: extreme`)
дают ровно противоположное: минимальную плотность при нереалистичной этажности,
одинаковой для ИЖС и для высотки. Поэтому таргеты задаёт оркестратор (ADR-0001, D4):
верхняя граница плотности при нормативном потолке этажности по группе.

Цели объёма (`residents`, `coverage_area`) GenBuilder ждёт абсолютными — на всю территорию.
Поэтому политика задаёт их удельно (чел/га и доля застроенности), а абсолютные значения
считаются здесь по фактической площади блоков каждой зоны. Зона без площади цели не получает
и честно попадает в предупреждение, а не тянет за собой случайное число.

Внутри сервиса таргеты живут по зонам (`{зона: {параметр: значение}}`) — так их удобно
собирать, показывать пользователю и переопределять. GenBuilder же читает их наизнанку
(`{параметр: {зона: значение}}`), поэтому перед отправкой их разворачивает
`to_genbuilder_targets`. Обе формы — `dict[str, dict[str, Any]]`, так что перепутанную
GenBuilder примет молча и просто проигнорирует, вернувшись к своим дефолтам.
"""

from typing import Any

from app.common.constants.pipeline_constants import (
    COVERAGE_RATIO_CAP,
    GENBUILDER_TARGET_PARAMETERS,
    GENPLANNER_TO_GENBUILDER_ZONE,
    PROFILE_TARGETS,
    RATE_TO_VOLUME_TARGET,
    RESIDENTS_PER_HECTARE_CAP,
    VOLUME_TARGET_BY_ZONE,
)
from app.pipeline.geo_area import SQUARE_METERS_IN_HECTARE

# Потолок этажности по группе — границы взяты из групп этажности самого GenBuilder
# (`BuildingType`: private 1–3, low 2–4, medium 5–8, high 9–16).
FLOORS_CAP_BY_GROUP: dict[str, int] = {"private": 2, "low": 4, "medium": 8, "high": 16}

# Чем застраивается зона, если её профиль не совпал с выбранным профилем территории:
# умереннее, чем профильная, — профиль задаёт баланс территории, а не единственный тип застройки.
FALLBACK_TARGETS_BY_ZONE: dict[str, dict[str, Any]] = {
    "residential": {
        "density_scenario": "mean",
        "floors_avg": 8,
        "default_floor_group": "medium",
        "residents_per_ha": 150,
    },
    "business": {"density_scenario": "mean", "floors_avg": 8, "default_floor_group": "high", "coverage_ratio": 0.20},
    "industrial": {"density_scenario": "mean", "floors_avg": 2, "coverage_ratio": 0.25},
    "transport": {"density_scenario": "mean", "floors_avg": 2, "coverage_ratio": 0.15},
    "special": {"density_scenario": "mean", "floors_avg": 2, "coverage_ratio": 0.15},
    "unknown": {"density_scenario": "mean", "floors_avg": 5, "default_floor_group": "medium", "coverage_ratio": 0.20},
}


def build_targets_by_zone(
    profile_id: int,
    zones_present: set[str],
    area_by_zone: dict[str, float] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Собирает таргеты по зонам — только для тех, которые реально есть в блоках.

    Зона выбранного профиля застраивается по максимуму, остальные — умеренно.
    `area_by_zone` — суммарная площадь блоков зоны в м²; из неё считаются
    абсолютные `residents` и `coverage_area`.
    """
    areas = area_by_zone or {}
    targets: dict[str, dict[str, Any]] = {}
    profile_mapping = GENPLANNER_TO_GENBUILDER_ZONE.get(profile_id)
    profile_zone = profile_mapping[0] if profile_mapping else None

    for zone in sorted(zones_present):
        if zone == profile_zone and profile_id in PROFILE_TARGETS:
            targets[zone] = dict(PROFILE_TARGETS[profile_id])
        else:
            targets[zone] = dict(FALLBACK_TARGETS_BY_ZONE.get(zone, FALLBACK_TARGETS_BY_ZONE["unknown"]))

    for zone, override in (overrides or {}).items():
        if zone in targets:
            targets[zone].update(override)

    for zone, target in targets.items():
        _clamp_floors(target)
        _resolve_volume_target(target, areas.get(zone, 0.0))

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


def _resolve_volume_target(target: dict[str, Any], area_m2: float) -> None:
    """Удельная величина + площадь -> абсолютная цель, которую понимает GenBuilder.

    Явно заданную абсолютную цель не трогаем: переопределение `residents` из запроса
    должно побеждать расчёт по площади.
    """
    hectares = max(area_m2, 0.0) / SQUARE_METERS_IN_HECTARE

    residents_per_ha = _positive_number(target.pop("residents_per_ha", None))
    if residents_per_ha is not None:
        cap = RESIDENTS_PER_HECTARE_CAP.get(str(target.get("default_floor_group")))
        if cap is not None:
            residents_per_ha = min(residents_per_ha, float(cap))
        target.setdefault("residents", round(residents_per_ha * hectares))

    coverage_ratio = _positive_number(target.pop("coverage_ratio", None))
    if coverage_ratio is not None:
        coverage_ratio = min(coverage_ratio, COVERAGE_RATIO_CAP)
        target.setdefault("coverage_area", round(coverage_ratio * max(area_m2, 0.0)))

    for key in RATE_TO_VOLUME_TARGET.values():
        if target.get(key) == 0:
            del target[key]


def _clamp_floors(target: dict[str, Any]) -> None:
    """«Разумные пределы»: этажность не выше потолка своей группы."""
    group = target.get("default_floor_group")
    cap = FLOORS_CAP_BY_GROUP.get(group) if isinstance(group, str) else None
    if cap is not None and isinstance(target.get("floors_avg"), (int, float)):
        target["floors_avg"] = min(int(target["floors_avg"]), cap)


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)
