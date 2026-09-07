"""Перевод территориальных зон GenPlanner в блоки GenBuilder.

GenBuilder знает только `residential / business / industrial / transport / special / unknown`
плюс группу этажности. Рекреации и сельского хозяйства в его таксономии нет —
такие зоны не застраиваются, и это надо честно показать пользователю, а не молча потерять.
"""

from dataclasses import dataclass, field
from typing import Any

from app.common.constants.pipeline_constants import (
    GENPLANNER_TO_GENBUILDER_ZONE,
    PROFILE_NAMES,
    ZONE_ID_PROPERTY_KEYS,
    ZONE_NAME_PROPERTY_KEYS,
    ZONE_NAME_TO_PROFILE,
)


@dataclass
class MappingResult:
    blocks: dict[str, Any]
    zones_present: set[str] = field(default_factory=set)
    buildable_count: int = 0
    skipped_by_profile: dict[int, int] = field(default_factory=dict)
    unrecognized_count: int = 0

    @property
    def total_count(self) -> int:
        return self.buildable_count + sum(self.skipped_by_profile.values()) + self.unrecognized_count

    def summary(self) -> dict[str, Any]:
        return {
            "total": self.total_count,
            "buildable": self.buildable_count,
            "skipped": {
                PROFILE_NAMES.get(profile_id, str(profile_id)): count
                for profile_id, count in sorted(self.skipped_by_profile.items())
            },
            "unrecognized": self.unrecognized_count,
            "zones": sorted(self.zones_present),
        }

    def warning_message(self) -> str | None:
        if not self.skipped_by_profile and not self.unrecognized_count:
            return None
        parts = [
            f"{PROFILE_NAMES.get(profile_id, profile_id)} — {count}"
            for profile_id, count in sorted(self.skipped_by_profile.items())
        ]
        if self.unrecognized_count:
            parts.append(f"не удалось определить зону — {self.unrecognized_count}")
        return (
            f"Из {self.total_count} зон застраивается {self.buildable_count}. " f"Не застраиваются: {', '.join(parts)}."
        )


def map_zones_to_blocks(zones: dict[str, Any]) -> MappingResult:
    """Переписывает `properties` каждой зоны в вид, который понимает GenBuilder."""
    result = MappingResult(blocks={"type": "FeatureCollection", "features": []})

    for feature in zones.get("features", []):
        properties = feature.get("properties") or {}
        profile_id = resolve_profile_id(properties)

        if profile_id is None:
            result.unrecognized_count += 1
            continue

        mapping = GENPLANNER_TO_GENBUILDER_ZONE.get(profile_id)
        if mapping is None:
            result.skipped_by_profile[profile_id] = result.skipped_by_profile.get(profile_id, 0) + 1
            continue

        zone, floors_group = mapping
        block_properties: dict[str, Any] = {
            **properties,
            "zone": zone,
            "source_profile_id": profile_id,
        }
        if floors_group:
            block_properties["floors_group"] = floors_group

        result.blocks["features"].append(
            {"type": "Feature", "geometry": feature.get("geometry"), "properties": block_properties}
        )
        result.zones_present.add(zone)
        result.buildable_count += 1

    return result


def resolve_profile_id(properties: dict[str, Any]) -> int | None:
    """Достаёт территориальную зону из свойств: сначала по id, потом по имени."""
    for key in ZONE_ID_PROPERTY_KEYS:
        raw = properties.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)

    for key in ZONE_NAME_PROPERTY_KEYS:
        raw = properties.get(key)
        if isinstance(raw, str):
            profile_id = ZONE_NAME_TO_PROFILE.get(raw.strip().lower())
            if profile_id is not None:
                return profile_id
    return None
