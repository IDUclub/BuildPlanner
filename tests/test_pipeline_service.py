"""Оркестрация целиком, на подставных клиентах: важен порядок событий и деградация."""

from typing import Any

import pytest
from fastapi import HTTPException

from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.pipeline_service import PipelineService

INDICATOR_VALUES = [
    {"indicator_id": 271, "value": 0.2, "date_value": "2025-01-01"},
    {"indicator_id": 274, "value": 0.9, "date_value": "2025-01-01"},
    {"indicator_id": 278, "value": 0.4, "date_value": "2025-01-01"},
]

ZONES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": None, "properties": {"territory_zone": 13}},
        {"type": "Feature", "geometry": None, "properties": {"territory_zone": 2}},
    ],
}


class FakeUrban:
    def __init__(self, values: list[dict[str, Any]] | None = None):
        self.values = INDICATOR_VALUES if values is None else values

    async def get_scenario_indicators(self, scenario_id: int, token: str) -> list[dict[str, Any]]:
        return self.values

    @staticmethod
    def latest_values_by_indicator(raw_values, indicator_ids):
        from app.clients.urban_api_client import UrbanApiClient

        return UrbanApiClient.latest_values_by_indicator(raw_values, indicator_ids)


class FakeGenPlanner:
    def __init__(self):
        self.calls = 0

    async def get_default_func_ratio(self, profile_id: int) -> dict[str, float]:
        return {"13": 0.7, "2": 0.3}

    async def run_func_generation(self, **kwargs) -> dict[str, Any]:
        self.calls += 1
        return {"zones": ZONES, "roads": {"type": "FeatureCollection", "features": []}}


class FakeGenBuilder:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.received_targets: dict[str, Any] | None = None

    async def generate_by_territory(self, token, blocks, targets_by_zone, **_kwargs) -> dict[str, Any]:
        if self.fail:
            raise HTTPException(status_code=500, detail={"msg": "builder down"})
        self.received_targets = targets_by_zone
        return {"buildings": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}}


def build_service(genbuilder: FakeGenBuilder | None = None, genplanner: FakeGenPlanner | None = None):
    return PipelineService(
        urban_client=FakeUrban(),
        genplanner_client=genplanner or FakeGenPlanner(),
        genbuilder_client=genbuilder or FakeGenBuilder(),
    )


async def collect(service: PipelineService, options: PipelineOptionsDTO | None = None) -> list[dict[str, Any]]:
    return [event async for event in service.stream(1, "token", options or PipelineOptionsDTO())]


@pytest.mark.asyncio
async def test_happy_path_event_order():
    events = await collect(build_service())
    types = [event["type"] for event in events]
    assert types.index("indicators") < types.index("profile_selected") < types.index("zones")
    assert types.index("zones") < types.index("result")
    assert "error" not in types


@pytest.mark.asyncio
async def test_zones_are_emitted_before_buildings():
    """Карту можно рисовать, не дожидаясь застройки."""
    events = await collect(build_service())
    zones_event = next(event for event in events if event["type"] == "zones")
    assert zones_event["source"] == "genplanner"


@pytest.mark.asyncio
async def test_selected_profile_drives_targets():
    genbuilder = FakeGenBuilder()
    await collect(build_service(genbuilder=genbuilder))
    assert genbuilder.received_targets["residential"]["floors_avg"] == 16  # профиль 13


@pytest.mark.asyncio
async def test_skipped_zones_produce_warning():
    events = await collect(build_service())
    warnings = [event for event in events if event["type"] == "warning"]
    assert any("рекреационная" in (event.get("message") or "") for event in warnings)


@pytest.mark.asyncio
async def test_genbuilder_failure_keeps_zones():
    service = build_service(genbuilder=FakeGenBuilder(fail=True))
    events = await collect(service)
    types = [event["type"] for event in events]
    assert "zones" in types
    assert "warning" in types
    assert "error" not in types


@pytest.mark.asyncio
async def test_skip_generation_stops_after_zones():
    events = await collect(build_service(), PipelineOptionsDTO(skip_generation=True))
    types = [event["type"] for event in events]
    assert "zones" in types
    assert "result" not in types


@pytest.mark.asyncio
async def test_manual_profile_skips_indicators():
    events = await collect(build_service(), PipelineOptionsDTO(profile_id=4))
    selected = next(event for event in events if event["type"] == "profile_selected")
    assert selected["profile_id"] == 4
    assert selected["scoreboard"] == []


@pytest.mark.asyncio
async def test_zones_are_cached_between_runs():
    genplanner = FakeGenPlanner()
    service = build_service(genplanner=genplanner)
    await collect(service)
    await collect(service)
    assert genplanner.calls == 1


@pytest.mark.asyncio
async def test_run_raises_when_scenario_has_no_indicator_values():
    service = PipelineService(FakeUrban(values=[]), FakeGenPlanner(), FakeGenBuilder())
    with pytest.raises(HTTPException) as exc_info:
        await service.run(1, "token", PipelineOptionsDTO())
    assert exc_info.value.status_code == 422
