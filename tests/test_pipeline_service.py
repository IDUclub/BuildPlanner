"""Оркестрация целиком, на подставных клиентах: важен порядок событий и деградация."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.clients.urban_api_client import UrbanApiClient
from app.common.constants.pipeline_constants import SELECTION_INDICATOR_IDS
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.pipeline_service import PipelineService


def _value_row(indicator_id: int, value: float) -> dict[str, Any]:
    """Форма `ScenarioIndicatorValue` из OpenAPI Urban API."""
    return {
        "indicator": {"indicator_id": indicator_id},
        "hexagon_id": None,
        "value": value,
        "updated_at": "2025-01-01T00:00:00Z",
    }


INDICATOR_VALUES = [_value_row(271, 0.2), _value_row(274, 0.9), _value_row(278, 0.4)]


def _square(lon: float, lat: float, side: float = 0.01) -> dict[str, Any]:
    """Квартал около Петербурга; площадь нужна, чтобы посчитать цели застройки."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + side, lat],
                [lon + side, lat + side],
                [lon, lat + side],
                [lon, lat],
            ]
        ],
    }


ZONES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": _square(30.0, 60.0), "properties": {"territory_zone": 13}},
        {"type": "Feature", "geometry": _square(30.02, 60.0), "properties": {"territory_zone": 2}},
    ],
}


class FakeUrban:
    def __init__(
        self,
        values: list[dict[str, Any]] | None = None,
        groups_fail: bool = False,
        region_id: int | None = 1,
    ):
        self.values = INDICATOR_VALUES if values is None else values
        self.groups_fail = groups_fail
        self.region_id = region_id
        self.project_fails = False
        self.project_ref_calls = 0
        self.requested_ids: list[Any] = []

    async def get_scenario_indicators(self, scenario_id: int, token: str, indicator_ids=None) -> list[dict[str, Any]]:
        self.requested_ids.append(indicator_ids)
        return self.values

    async def get_indicator_groups(self, token: str) -> list[dict[str, Any]]:
        if self.groups_fail:
            raise RuntimeError("справочник недоступен")
        return [{"name": "demogrphy", "indicators": [{"indicator_id": 271}]}]

    async def get_project_ref(self, scenario_id: int, token: str) -> tuple[int, int | None]:
        self.project_ref_calls += 1
        if self.project_fails:
            raise HTTPException(status_code=502, detail={"msg": "Urban API недоступен"})
        return 120, self.region_id

    @staticmethod
    def latest_values_by_indicator(raw_values, indicator_ids):
        return UrbanApiClient.latest_values_by_indicator(raw_values, indicator_ids)

    @staticmethod
    def latest_rows_by_indicator(raw_values, indicator_ids=None):
        return UrbanApiClient.latest_rows_by_indicator(raw_values, indicator_ids)


class FakeGenPlanner:
    def __init__(self):
        self.calls = 0
        self.received: dict[str, Any] = {}
        self.zones = ZONES

    async def get_default_func_ratio(self, profile_id: int) -> dict[str, float]:
        return {"13": 0.7, "2": 0.3}

    async def run_func_generation(self, **kwargs) -> dict[str, Any]:
        self.calls += 1
        self.received = kwargs
        return {"zones": self.zones, "roads": {"type": "FeatureCollection", "features": []}}


class FakeGenBuilder:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.received_targets: dict[str, Any] | None = None
        self.received_territory_id: int | None = None

    async def generate_by_territory(
        self, token, blocks, targets_by_zone, territory_id=None, **_kwargs
    ) -> dict[str, Any]:
        if self.fail:
            raise HTTPException(status_code=500, detail={"msg": "builder down"})
        self.received_targets = targets_by_zone
        self.received_territory_id = territory_id
        return {"buildings": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}}


def build_service(
    genbuilder: FakeGenBuilder | None = None,
    genplanner: FakeGenPlanner | None = None,
    urban: FakeUrban | None = None,
):
    return PipelineService(
        urban_client=urban or FakeUrban(),
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
    """Наружу уходит форма GenBuilder: параметр снаружи, зона внутри."""
    genbuilder = FakeGenBuilder()
    await collect(build_service(genbuilder=genbuilder))
    assert genbuilder.received_targets["floors_avg"]["residential"] == 16  # профиль 13
    assert genbuilder.received_targets["residents"]["residential"] > 0


@pytest.mark.asyncio
async def test_residents_target_follows_the_block_area():
    """Цель считается от площади блоков, а не берётся константой."""
    genbuilder = FakeGenBuilder()
    await collect(build_service(genbuilder=genbuilder))
    residential_area_ha = 0.01 * 0.01 * 111_320 * 111_320 * 0.5 / 10_000  # ~62 га на широте 60°
    expected = round(420 * residential_area_ha)  # 420 чел/га у многоэтажной застройки
    assert genbuilder.received_targets["residents"]["residential"] == pytest.approx(expected, rel=0.02)


def test_residents_are_not_asked_for():
    """Число жителей — расчётная величина, а не параметр прогона."""
    assert "residents" not in PipelineOptionsDTO.model_fields


@pytest.mark.asyncio
async def test_overrides_remain_the_manual_escape_hatch():
    """Автоматический расчёт можно перебить точечно — этот путь остаётся."""
    genbuilder = FakeGenBuilder()
    await collect(
        build_service(genbuilder=genbuilder),
        PipelineOptionsDTO(targets_overrides={"residential": {"residents": 7000}}),
    )
    assert genbuilder.received_targets["residents"]["residential"] == 7000


@pytest.mark.asyncio
async def test_blocks_without_geometry_warn_instead_of_inventing_a_target():
    genplanner = FakeGenPlanner()
    genplanner.zones = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": None, "properties": {"territory_zone": 13}}],
    }
    events = await collect(build_service(genplanner=genplanner))
    warnings = [event for event in events if event["type"] == "warning"]
    assert any(event.get("detail") == "no_volume_target" for event in warnings)


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
async def test_indicators_are_filtered_on_the_urban_api_side():
    """У сценария сотни значений, включая гексагональные — для выбора тянем только десять."""
    urban = FakeUrban()
    service = PipelineService(urban, FakeGenPlanner(), FakeGenBuilder())
    await collect(service)
    assert urban.requested_ids[0] == SELECTION_INDICATOR_IDS


@pytest.mark.asyncio
async def test_territory_indicators_are_a_separate_unfiltered_request():
    """Витрина показывает всё, что есть у сценария, а выбор профиля остаётся узким."""
    urban = FakeUrban()
    service = PipelineService(urban, FakeGenPlanner(), FakeGenBuilder())
    events = await collect(service)
    assert urban.requested_ids == [SELECTION_INDICATOR_IDS, None]
    overview = next(event for event in events if event["type"] == "territory_indicators")
    assert overview["total"] == 3
    assert overview["sections"][0]["title"] == "Демография"


@pytest.mark.asyncio
async def test_territory_indicators_come_before_the_selected_profile():
    """Сначала показываем, что за территория, потом — что на ней решили строить."""
    types = [event["type"] for event in await collect(build_service())]
    assert types.index("territory_indicators") < types.index("profile_selected")


@pytest.mark.asyncio
async def test_broken_indicator_catalogue_does_not_fail_the_run():
    """Витрина справочная: без неё прогон обязан дойти до застройки."""
    service = PipelineService(FakeUrban(groups_fail=True), FakeGenPlanner(), FakeGenBuilder())
    types = [event["type"] for event in await collect(service)]
    assert "territory_indicators" not in types
    assert "result" in types
    assert "error" not in types


@pytest.mark.asyncio
async def test_project_id_is_resolved_from_the_scenario():
    """`GenPlannerFuncZonesDTO.project_id` обязателен — без него ручка отдаёт 422."""
    genplanner = FakeGenPlanner()
    await collect(build_service(genplanner=genplanner))
    assert genplanner.received["project_id"] == 120


@pytest.mark.asyncio
async def test_explicit_project_id_wins_over_lookup():
    genplanner = FakeGenPlanner()
    await collect(build_service(genplanner=genplanner), PipelineOptionsDTO(project_id=7))
    assert genplanner.received["project_id"] == 7


def _services_warnings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "warning" and event["detail"] == "services_region_unknown"]


@pytest.mark.asyncio
async def test_project_region_reaches_genbuilder():
    """По нормативам региона GenBuilder расставляет школы и сады в жилых кварталах."""
    genbuilder = FakeGenBuilder()
    events = await collect(build_service(genbuilder=genbuilder))
    assert genbuilder.received_territory_id == 1
    assert not _services_warnings(events)


@pytest.mark.asyncio
async def test_project_and_region_come_from_one_lookup():
    urban = FakeUrban()
    await collect(build_service(urban=urban))
    assert urban.project_ref_calls == 1


@pytest.mark.asyncio
async def test_unknown_region_warns_that_services_are_missing():
    genbuilder = FakeGenBuilder()
    events = await collect(build_service(genbuilder=genbuilder, urban=FakeUrban(region_id=None)))
    assert genbuilder.received_territory_id is None
    assert _services_warnings(events)
    assert "result" in [event["type"] for event in events]


@pytest.mark.asyncio
async def test_no_services_warning_without_residential_blocks():
    """Сервисы ставятся только в жилых кварталах — без них предупреждать не о чем."""
    genplanner = FakeGenPlanner()
    genplanner.zones = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": _square(30.0, 60.0), "properties": {"territory_zone": 4}}],
    }
    genbuilder = FakeGenBuilder()
    events = await collect(build_service(genbuilder=genbuilder, genplanner=genplanner, urban=FakeUrban(region_id=None)))
    assert genbuilder.received_targets is not None
    assert not _services_warnings(events)


@pytest.mark.asyncio
async def test_region_is_resolved_when_zones_come_from_cache():
    """Кэш зон пропускает запрос проекта для GenPlanner, но регион GenBuilder всё равно нужен."""
    urban, genbuilder = FakeUrban(), FakeGenBuilder()
    service = build_service(genbuilder=genbuilder, urban=urban)
    await collect(service)
    genbuilder.received_territory_id = None
    await collect(service)
    assert urban.project_ref_calls == 2
    assert genbuilder.received_territory_id == 1


@pytest.mark.asyncio
async def test_failed_region_lookup_costs_only_the_services():
    urban, genbuilder = FakeUrban(), FakeGenBuilder()
    service = build_service(genbuilder=genbuilder, urban=urban)
    await collect(service)
    urban.project_fails = True
    events = await collect(service)
    types = [event["type"] for event in events]
    assert "result" in types
    assert "error" not in types
    assert _services_warnings(events)


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


class FakePublisher:
    def __init__(self, fail: bool = False, published: dict[str, Any] | None = None):
        self.fail = fail
        self.published = published or {"project_id": 900, "scenario_id": 777, "notified": True}
        self.received: dict[str, Any] | None = None

    async def publish(self, **kwargs):
        if self.fail:
            raise HTTPException(status_code=403, detail={"msg": "нет прав"})
        self.received = kwargs
        return SimpleNamespace(as_dict=lambda: self.published)


@pytest.mark.asyncio
async def test_published_scenario_closes_the_run():
    publisher = FakePublisher()
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    types = [event["type"] for event in await collect(service)]
    assert types.index("result") < types.index("scenario_published")
    assert publisher.received["profile_name"] == "жилая многоэтажная"


@pytest.mark.asyncio
async def test_failed_publication_does_not_lose_the_generation():
    """Зоны и застройка уже у пользователя — потерять их из-за Urban API нельзя."""
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=FakePublisher(fail=True))
    events = await collect(service)
    types = [event["type"] for event in events]
    assert "result" in types
    assert "error" not in types
    assert any(event.get("stage") == "publish_scenario" for event in events if event["type"] == "warning")


@pytest.mark.asyncio
async def test_partially_saved_scenario_is_named_in_the_warning():
    """The scenario exists in Urban API even if a later step failed — the user must get its id."""
    publisher = FakePublisher(
        published={
            "project_id": 900,
            "scenario_id": 777,
            "notified": False,
            "failed_stage": "functional_zones",
            "error": "нет прав",
        }
    )
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    events = await collect(service)
    published = next(event for event in events if event["type"] == "scenario_published")
    assert published["scenario_id"] == 777
    warnings = [event for event in events if event["type"] == "warning" and event["stage"] == "publish_scenario"]
    assert any("777" in event["message"] for event in warnings)


@pytest.mark.asyncio
async def test_lost_buildings_and_unknown_services_are_reported():
    publisher = FakePublisher(
        published={
            "project_id": 900,
            "scenario_id": 777,
            "notified": True,
            "buildings_failed": 3,
            "buildings_total": 10,
            "unknown_service_names": ["Космодром"],
        }
    )
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    details = {event["detail"] for event in await collect(service) if event["type"] == "warning"}
    assert {"buildings_failed", "unknown_service_types"} <= details


@pytest.mark.asyncio
async def test_result_summary_counts_what_was_built():
    events = await collect(build_service())
    summary = next(event for event in events if event["type"] == "result")["summary"]
    assert summary["buildings"] == 1
    assert {"residents", "living_area_m2", "building_area_m2", "services", "buildings_by_zone"} <= set(summary)
    assert summary["profile"] == "жилая многоэтажная"


@pytest.mark.asyncio
async def test_zones_are_published_even_when_the_builder_failed():
    """Оценки считаются и по одним зонам — незачёт застройки не отменяет расчёт."""
    publisher = FakePublisher()
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(fail=True), publisher=publisher)
    types = [event["type"] for event in await collect(service)]
    assert "scenario_published" in types
    assert publisher.received["buildings"] is None


@pytest.mark.asyncio
async def test_publication_can_be_turned_off_per_run():
    publisher = FakePublisher()
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    types = [event["type"] for event in await collect(service, PipelineOptionsDTO(publish=False))]
    assert "scenario_published" not in types
    assert publisher.received is None


@pytest.mark.asyncio
async def test_run_raises_when_scenario_has_no_indicator_values():
    service = PipelineService(FakeUrban(values=[]), FakeGenPlanner(), FakeGenBuilder())
    with pytest.raises(HTTPException) as exc_info:
        await service.run(1, "token", PipelineOptionsDTO())
    assert exc_info.value.status_code == 422
