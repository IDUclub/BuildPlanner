"""Оркестрация целиком, на подставных клиентах: важен порядок событий и деградация."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.clients.urban_api_client import UrbanApiClient
from app.common.constants.pipeline_constants import SELECTION_INDICATOR_IDS
from app.common.object_storage.object_storage import LocalStorage, ObjectStorageError
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.geo_layers import LayerStore
from app.pipeline.pipeline_service import PipelineService, PostPublishStages, _services_warning


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


def test_services_warning_reports_type_not_supported_and_demand_below_template():
    """GenBuilder 0.1.4 (genbuilder_api#54) добавил эти причины отдельно от `unplaced_no_template`."""
    message = _services_warning(
        {
            "services_requested": 3,
            "services_placed": 1,
            "unplaced_type_not_supported": 1,
            "unplaced_no_template": 0,
            "unplaced_demand_below_template": 1,
            "unplaced_no_space": 0,
            "unplaced_site_limit": 0,
            "capacity_requested": 300.0,
            "capacity_unplaced": 80.0,
        }
    )
    assert message is not None
    assert "причина не указана" not in message
    assert "тип не поддерживается — 1" in message
    assert "спрос меньше здания — 1" in message


def test_services_warning_without_new_fields_still_reports_old_reasons():
    """Старые ответы GenBuilder (без новых полей) не должны ломать причины."""
    message = _services_warning(
        {
            "services_requested": 2,
            "services_placed": 1,
            "unplaced_no_template": 1,
            "unplaced_no_space": 0,
            "unplaced_site_limit": 0,
        }
    )
    assert message is not None
    assert "нет шаблона — 1" in message


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


def _publish_warnings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "warning" and event["stage"] == "publish_scenario"]


@pytest.mark.asyncio
async def test_lost_services_are_reported_apart_from_buildings():
    publisher = FakePublisher(published={"project_id": 900, "scenario_id": 777, "notified": True, "services_failed": 2})
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    warnings = _publish_warnings(await collect(service))
    lost = next(event for event in warnings if event["detail"] == "services_failed")
    assert "2" in lost["message"]
    assert "buildings_failed" not in {event["detail"] for event in warnings}


@pytest.mark.asyncio
async def test_broker_failure_says_the_data_is_written_and_what_is_scored():
    """Only the announcement is missing: «written partly» would send the user looking for losses."""
    publisher = FakePublisher(
        published={
            "project_id": 900,
            "scenario_id": 777,
            "notified": False,
            "notified_events": ["scenario_zones_updated"],
            "failed_stage": "broker",
            "error": "broker unavailable",
        }
    )
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    warning = next(
        event for event in _publish_warnings(await collect(service)) if event["detail"] == "broker unavailable"
    )
    assert "777" in warning["message"]
    assert "записаны" in warning["message"]
    assert "не полностью" not in warning["message"]
    assert "только по функциональным зонам" in warning["message"]


@pytest.mark.asyncio
async def test_broker_failure_before_any_message_says_scoring_did_not_start():
    publisher = FakePublisher(
        published={"project_id": 900, "scenario_id": 777, "notified": False, "failed_stage": "broker"}
    )
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), publisher=publisher)
    warning = next(event for event in _publish_warnings(await collect(service)) if event["detail"] == "broker")
    assert "расчёт оценок не запущен" in warning["message"]


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


class BrokenStorage(LocalStorage):
    def put_json(self, payload, object_key):
        raise ObjectStorageError("бакет недоступен")


def _files(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "file"]


@pytest.mark.asyncio
async def test_every_layer_is_stored_right_after_it_is_streamed(tmp_path):
    """Живой поток рисует карту сразу, а ссылки нужны, чтобы перерисовать её из истории."""
    storage = LocalStorage(str(tmp_path))
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), layer_store=LayerStore(storage))
    events = [event async for event in service.stream(1, "token", PipelineOptionsDTO(), base_url="http://bp/")]
    types = [event["type"] for event in events]

    assert [event["content"]["name"] for event in _files(events)] == ["zones", "roads", "buildings"]
    assert types.index("file") < types.index("zones") < types.index("roads")
    assert types.index("file", types.index("roads")) < types.index("result")
    result_id = _files(events)[0]["content"]["url"].rsplit("/", 1)[-1]
    assert _files(events)[0]["content"]["url"] == f"http://bp/buildplanner/files/zones/{result_id}"
    assert storage.exists(f"{result_id}/buildings.geojson")


class FakeGenPlannerWithRoads(FakeGenPlanner):
    ROADS = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[30.0, 60.0], [30.01, 60.0]]},
                "properties": {"road_lvl": "local road, level 1", "roads_width": 6.0},
            }
        ],
    }

    async def run_func_generation(self, **kwargs) -> dict[str, Any]:
        return {**await super().run_func_generation(**kwargs), "roads": self.ROADS}


def _stored(storage: LocalStorage, url: str, name: str) -> dict[str, Any]:
    result_id = url.rsplit("/", 1)[-1]
    return json.loads(b"".join(storage.open_stream(f"{result_id}/{name}.geojson")))


@pytest.mark.asyncio
async def test_map_gets_russian_attributes_while_the_pipeline_keeps_raw_zones(tmp_path):
    storage = LocalStorage(str(tmp_path))
    service = PipelineService(FakeUrban(), FakeGenPlannerWithRoads(), FakeGenBuilder(), layer_store=LayerStore(storage))
    events = [event async for event in service.stream(1, "token", PipelineOptionsDTO(), base_url="http://bp/")]
    by_type = {event["type"]: event for event in events}

    zone_descriptor = by_type["zones"]["content"]
    road_descriptor = by_type["roads"]["content"]
    files = {event["content"]["name"]: event["content"]["url"] for event in _files(events)}
    zones = _stored(storage, files["zones"], "zones")
    roads = _stored(storage, files["roads"], "roads")
    zone_labels = [feature["properties"] for feature in zones["features"]]
    assert zone_labels == [{"Территориальная зона": "жилая"}, {"Территориальная зона": "рекреационная"}]
    road = roads["features"][0]["properties"]
    assert road == {"Ширина, м": 6.0, "road_lvl": "local road, level 1", "road_class": "street"}

    assert zone_descriptor["url"] == files["zones"]
    assert road_descriptor["url"] == files["roads"]
    assert "result" in by_type, "GenBuilder должен получить блоки из исходных зон"

    result = await PipelineService(FakeUrban(), FakeGenPlannerWithRoads(), FakeGenBuilder()).run(
        1, "token", PipelineOptionsDTO()
    )
    assert result.zones["features"][0]["properties"] == {"territory_zone": 13}
    assert result.roads["features"][0]["properties"]["roads_width"] == 6.0


@pytest.mark.asyncio
async def test_storage_failure_warns_once_and_keeps_the_run(tmp_path):
    layer_store = LayerStore(BrokenStorage(str(tmp_path)))
    service = PipelineService(FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), layer_store=layer_store)
    events = await collect(service)
    types = [event["type"] for event in events]

    assert "result" not in types
    assert "error" not in types
    assert not _files(events)
    assert len([event for event in events if event["type"] == "warning" and event["stage"] == "store_layer"]) == 1


@pytest.mark.asyncio
async def test_synchronous_run_does_not_store_layers(tmp_path):
    """У синхронного ответа нет истории — писать слои незачем."""
    service = PipelineService(
        FakeUrban(), FakeGenPlanner(), FakeGenBuilder(), layer_store=LayerStore(LocalStorage(str(tmp_path)))
    )
    await service.run(1, "token", PipelineOptionsDTO())
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_run_raises_when_scenario_has_no_indicator_values():
    service = PipelineService(FakeUrban(values=[]), FakeGenPlanner(), FakeGenBuilder())
    with pytest.raises(HTTPException) as exc_info:
        await service.run(1, "token", PipelineOptionsDTO())
    assert exc_info.value.status_code == 422


SCHEDULE_ANSWER = {
    "provision": {
        "house_construction_period": {"1": 1},
        "service_construction_period": {"9": 1},
        "houses_per_period": [2],
        "services_per_period": [1],
        "houses_area_per_period": [9000],
        "services_area_per_period": [800],
        "provided_per_period": [0.9],
        "periods": [1],
        "buildings_comment": None,
        "services_comment": None,
    },
    "simple": None,
}

PROVISION_ANSWER = {"periods": [1], "provision": [{"школа": 0.9}], "unbuilt_services": []}

# Сценарий, по которому очередь посчитать можно: записан целиком, есть и дома, и сервисы.
READY_PUBLISHED = {
    "project_id": 900,
    "scenario_id": 777,
    "notified": True,
    "living_buildings_written": 3,
    "services_written": 2,
}


class FakeSirtep:
    def __init__(self, schedule_error: Exception | None = None, provision_error: Exception | None = None):
        self.schedule_error = schedule_error
        self.provision_error = provision_error
        self.schedule_calls: list[dict[str, Any]] = []
        self.provision_calls: list[dict[str, Any]] = []

    async def schedule(self, *, scenario_id, periods=None, max_area_per_period=None):
        self.schedule_calls.append(
            {"scenario_id": scenario_id, "periods": periods, "max_area_per_period": max_area_per_period}
        )
        if self.schedule_error:
            raise self.schedule_error
        return SCHEDULE_ANSWER

    async def provision(self, *, scenario_id, periods=None, max_area_per_period=None):
        self.provision_calls.append(
            {"scenario_id": scenario_id, "periods": periods, "max_area_per_period": max_area_per_period}
        )
        if self.provision_error:
            raise self.provision_error
        return PROVISION_ANSWER


def build_with_sirtep(sirtep: FakeSirtep, published: dict[str, Any] | None = None) -> PipelineService:
    return PipelineService(
        FakeUrban(),
        FakeGenPlanner(),
        FakeGenBuilder(),
        publisher=FakePublisher(published=published or READY_PUBLISHED),
        post_publish=PostPublishStages(sirtep=sirtep),
    )


def _skip_warnings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "warning" and event["detail"] == "sirtep_skipped"]


@pytest.mark.asyncio
async def test_queue_is_built_on_the_published_scenario():
    """SIRTEP читает сценарий из Urban API — звать его раньше публикации нечем."""
    sirtep = FakeSirtep()
    events = await collect(build_with_sirtep(sirtep))
    types = [event["type"] for event in events]
    assert types.index("scenario_published") < types.index("sirtep_schedule") < types.index("sirtep_provision")
    assert sirtep.schedule_calls[0]["scenario_id"] == 777


@pytest.mark.asyncio
async def test_queue_is_skipped_without_dwellings():
    """Без домов SIRTEP отвечает 400, и это ничего пользователю не объясняет."""
    published = {**READY_PUBLISHED, "living_buildings_written": 0}
    sirtep = FakeSirtep()
    events = await collect(build_with_sirtep(sirtep, published))
    assert not sirtep.schedule_calls
    assert "жилых домов" in _skip_warnings(events)[0]["message"]


@pytest.mark.asyncio
async def test_queue_is_skipped_without_services():
    """Без сервисов ветка обеспеченности падает 500."""
    published = {**READY_PUBLISHED, "services_written": 0}
    sirtep = FakeSirtep()
    events = await collect(build_with_sirtep(sirtep, published))
    assert not sirtep.schedule_calls
    assert "сервисов" in _skip_warnings(events)[0]["message"]


@pytest.mark.asyncio
async def test_broker_failure_does_not_block_the_queue():
    """Данные сценария записаны, не хватает только объявления — SIRTEP брокер и не нужен."""
    published = {**READY_PUBLISHED, "failed_stage": "broker", "notified": False}
    sirtep = FakeSirtep()
    await collect(build_with_sirtep(sirtep, published))
    assert sirtep.schedule_calls


@pytest.mark.asyncio
async def test_partially_written_scenario_blocks_the_queue():
    """Очередь по неполному сценарию — это ответ про территорию, которой нет."""
    published = {**READY_PUBLISHED, "failed_stage": "buildings"}
    sirtep = FakeSirtep()
    events = await collect(build_with_sirtep(sirtep, published))
    assert not sirtep.schedule_calls
    assert "не полностью" in _skip_warnings(events)[0]["message"]


@pytest.mark.asyncio
async def test_sirtep_failure_keeps_the_rest_of_the_run():
    sirtep = FakeSirtep(schedule_error=HTTPException(status_code=503, detail={"msg": "sirtep down"}))
    events = await collect(build_with_sirtep(sirtep))
    types = [event["type"] for event in events]
    assert "error" not in types
    assert "result" in types and "master_plan_summary" in types
    assert any(event["stage"] == "sirtep" for event in events if event["type"] == "warning")


@pytest.mark.asyncio
async def test_provision_timeout_keeps_the_queue():
    """Очередь уже посчитана — терять её из-за недосчитанных ТЭПов нельзя."""
    sirtep = FakeSirtep(provision_error=HTTPException(status_code=504, detail={"msg": "не успел"}))
    events = await collect(build_with_sirtep(sirtep))
    types = [event["type"] for event in events]
    assert "sirtep_schedule" in types
    assert "sirtep_provision" not in types
    assert "error" not in types


@pytest.mark.asyncio
async def test_provision_can_be_waived_by_option():
    sirtep = FakeSirtep()
    events = await collect(build_with_sirtep(sirtep), PipelineOptionsDTO(sirtep_wait_provision=False))
    assert not sirtep.provision_calls
    assert "sirtep_schedule" in [event["type"] for event in events]


@pytest.mark.asyncio
async def test_pace_options_reach_sirtep():
    sirtep = FakeSirtep()
    options = PipelineOptionsDTO(sirtep_periods=8, sirtep_max_area_per_period=25_000)
    await collect(build_with_sirtep(sirtep), options)
    assert sirtep.schedule_calls[0]["periods"] == 8
    assert sirtep.provision_calls[0]["max_area_per_period"] == 25_000


@pytest.mark.asyncio
async def test_summary_closes_every_run():
    """Справка приходит последней и без SIRTEP: застройка и предупреждения в ней есть всегда."""
    events = await collect(build_service())
    assert events[-1]["type"] == "master_plan_summary"
    assert events[-1]["buildings"]["buildings"] == 1
    assert events[-1]["schedule"] is None
