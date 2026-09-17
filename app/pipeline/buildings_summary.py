"""What a GenBuilder result amounts to: buildings, residents, floor areas and services."""

from collections import Counter
from typing import Any, Sequence

from app.clients.genbuilder_client import building_services

NO_ZONE = "unassigned"


def summarize_buildings(features: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_zone: Counter[str] = Counter()
    services: dict[str, dict[str, float]] = {}
    residents = living_area = building_area = 0.0

    for feature in features:
        properties = feature.get("properties") or {}
        by_zone[str(properties.get("zone") or NO_ZONE)] += 1
        residents += _number(properties.get("residents_number"))
        living_area += _number(properties.get("living_area"))
        building_area += _number(properties.get("building_area"))
        for name, capacity in building_services(feature):
            entry = services.setdefault(name, {"count": 0.0, "capacity": 0.0})
            entry["count"] += 1
            entry["capacity"] += _number(capacity)

    return {
        "buildings": len(features),
        "buildings_by_zone": dict(by_zone),
        "residents": round(residents),
        "living_area_m2": round(living_area),
        "building_area_m2": round(building_area),
        "services": [
            {"name": name, "count": int(entry["count"]), "capacity": round(entry["capacity"])}
            for name, entry in sorted(services.items())
        ],
    }


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
