"""Запись сгенерированного сценария в Urban API и объявление о нём в брокер.

Главное, что здесь проверяется, — порядок: сообщения в брокер уходят последними.
Сервисы оценок по сообщению идут читать сценарий, и опередить запись данных нельзя.

Справочники подставлены в той же форме, в какой их отдаёт стенд: имена типов зон —
английские слаги, русское название лежит в `zone_nickname`.
"""

import base64
import json
from typing import Any

import pytest
from fastapi import HTTPException

from app.clients.urban_scenario_writer import UrbanScenarioWriter
from app.common.auth.service_token import _subject
from app.pipeline.scenario_publisher import ScenarioPublisher

ZONE_TYPES = [
    {"functional_zone_type_id": 2, "name": "recreation", "zone_nickname": "Рекреационная зона"},
    {"functional_zone_type_id": 7, "name": "business", "zone_nickname": "Общественно-деловая зона"},
    {"functional_zone_type_id": 13, "name": "residential_multistorey", "zone_nickname": "Многоэтажная жилая зона"},
]
OBJECT_TYPES = [
    {"physical_object_type_id": 4, "name": "Жилой дом"},
    {"physical_object_type_id": 5, "name": "Нежилое здание"},
    {"physical_object_type_id": 52, "name": "Местная дорога"},
]


def _zone(territory_zone: Any = None, name: str | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    if territory_zone is not None:
        properties["territory_zone"] = territory_zone
    if name is not None:
        properties["territory_zone_name"] = name
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": properties}


def _building(zone: str = "residential", **properties: Any) -> dict[str, Any]:
    properties["zone"] = zone
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [30.0, 60.0]}, "properties": properties}


class FakeHandler:
    """Пишет в журнал каждый вызов — по нему и проверяется порядок."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_on = fail_on

    async def get(self, path: str, params=None, headers=None) -> Any:
        self.calls.append(("GET", path, None))
        if path.endswith("/functional_zones_types"):
            return ZONE_TYPES
        if path.endswith("/physical_object_types"):
            return OBJECT_TYPES
        if path.endswith("/territory"):
            return {"geometry": {"type": "Polygon", "coordinates": []}}
        return []

    async def post(self, path: str, json_data=None, params=None, headers=None) -> Any:
        self.calls.append(("POST", path, json_data))
        if self.fail_on and self.fail_on in path:
            raise HTTPException(status_code=403, detail={"msg": "нет прав"})
        if path == "/api/v1/projects":
            return {"project_id": 900}
        if path.endswith("/physical_objects"):
            return {"physical_object": {"physical_object_id": 555}}
        if path.startswith("/api/v1/scenarios/") and path.count("/") == 4:
            return {"scenario_id": 777}
        if path.endswith("/functional_zones"):
            return json_data
        return {}

    def paths(self, method: str = "POST") -> list[str]:
        return [path for verb, path, _ in self.calls if verb == method]

    def payload(self, suffix: str) -> Any:
        return next(body for verb, path, body in self.calls if verb == "POST" and path.endswith(suffix))

    def payloads(self, suffix: str) -> list[Any]:
        return [body for verb, path, body in self.calls if verb == "POST" and path.endswith(suffix)]


class RoadsOnlyHandler(FakeHandler):
    """Справочник без единого подходящего типа под здание."""

    async def get(self, path: str, params=None, headers=None) -> Any:
        await super().get(path, params, headers)
        return [{"physical_object_type_id": 52, "name": "Местная дорога"}]


class FakeTokens:
    def __init__(self, user_id: str = "svc-1"):
        self.user_id = user_id

    async def get_token(self) -> str:
        return "service-token"

    async def get_user_id(self) -> str:
        return self.user_id


class FakeUrbanReader:
    def __init__(self, region_id: int | None = 42):
        self.region_id = region_id
        self.ref_calls = 0

    async def get_project_ref(self, scenario_id: int, token: str) -> tuple[int, int | None]:
        self.ref_calls += 1
        return 120, self.region_id

    async def get_project_geometry(self, project_id: int, token: str) -> dict[str, Any]:
        return {"type": "Polygon", "coordinates": []}


def build_writer(handler: FakeHandler | None = None) -> tuple[UrbanScenarioWriter, FakeHandler]:
    handler = handler or FakeHandler()
    return UrbanScenarioWriter(handler, FakeTokens()), handler


def build_publisher(handler: FakeHandler | None = None, reader: FakeUrbanReader | None = None):
    writer, handler = build_writer(handler)
    reader = reader or FakeUrbanReader()
    return ScenarioPublisher(reader, writer), handler, reader


async def publish(publisher: ScenarioPublisher, zones=None, buildings=None):
    return await publisher.publish(
        source_scenario_id=835,
        user_token="user-token",
        profile_id=13,
        profile_name="жилая многоэтажная",
        year=2026,
        zones=zones if zones is not None else {"features": [_zone(13)]},
        buildings=buildings,
    )


# --------------------------------------------------------------------- типы зон


@pytest.mark.asyncio
async def test_zone_id_is_the_same_number_on_both_sides():
    """`territory_zone` GenPlanner'а и `functional_zone_type_id` Urban API — одно пространство id."""
    writer, handler = build_writer()
    written, skipped = await writer.add_functional_zones(7, [_zone(13), _zone(2)], year=2026)
    assert (written, skipped) == (2, [])
    assert handler.paths().count("/api/v1/scenarios/7/functional_zones") == 1
    assert [zone["functional_zone_type_id"] for zone in handler.payload("/functional_zones")] == [13, 2]


@pytest.mark.asyncio
async def test_zone_without_id_falls_back_to_the_catalogue_nickname():
    writer, handler = build_writer()
    written, skipped = await writer.add_functional_zones(7, [_zone(name="Многоэтажная жилая зона")], year=2026)
    assert (written, skipped) == (1, [])
    assert handler.payload("/functional_zones")[0]["functional_zone_type_id"] == 13


@pytest.mark.asyncio
async def test_genplanner_own_zone_name_is_translated_by_our_table():
    """Имён GenPlanner'а в справочнике стенда нет — их переводит `ZONE_NAME_TO_PROFILE`."""
    writer, handler = build_writer()
    written, skipped = await writer.add_functional_zones(7, [_zone(name="общественно-деловая")], year=2026)
    assert (written, skipped) == (1, [])
    assert handler.payload("/functional_zones")[0]["functional_zone_type_id"] == 7


@pytest.mark.asyncio
async def test_zone_id_unknown_to_the_stand_is_skipped_not_guessed():
    """`functional_zone_type_id` обязателен, но чужой id подставлять нельзя."""
    writer, _ = build_writer()
    written, skipped = await writer.add_functional_zones(
        7, [_zone(13), _zone(999, name="Зона неизвестного назначения")], year=2026
    )
    assert written == 1
    assert skipped == ["Зона неизвестного назначения"]


# --------------------------------------------------------------------- здания


@pytest.mark.asyncio
async def test_building_takes_two_requests_and_reuses_the_returned_id():
    writer, handler = build_writer()
    written, types = await writer.add_buildings(7, territory_id=42, features=[_building(storeys_count=16)])
    assert (written, types) == (1, [4])
    assert handler.paths() == ["/api/v1/scenarios/7/physical_objects", "/api/v1/scenarios/7/buildings"]
    building = handler.payload("/buildings")
    assert building["physical_object_id"] == 555
    assert building["floors"] == 16
    assert building["is_scenario_object"] is True


@pytest.mark.asyncio
async def test_residential_and_other_buildings_get_different_types():
    """Свести жильё и общественно-деловую застройку в один тип — соврать о назначении."""
    writer, handler = build_writer()
    written, types = await writer.add_buildings(
        7, territory_id=42, features=[_building("residential"), _building("business")]
    )
    assert (written, types) == (2, [4, 5])
    assert sorted(body["physical_object_type_id"] for body in handler.payloads("/physical_objects")) == [4, 5]


@pytest.mark.asyncio
async def test_missing_building_type_names_what_is_available():
    """Молча выбрать «какой-нибудь» тип нельзя — здания уедут не туда."""
    writer, _ = build_writer(RoadsOnlyHandler())
    with pytest.raises(HTTPException) as exc_info:
        await writer.building_type_id("residential")
    assert exc_info.value.status_code == 502
    assert "дорога" in json.dumps(exc_info.value.detail, ensure_ascii=False).lower()


# --------------------------------------------------------------------- порядок публикации


@pytest.mark.asyncio
async def test_broker_is_notified_after_the_data_is_written():
    """Сервисы оценок по сообщению идут читать сценарий — опередить запись нельзя."""
    publisher, handler, _ = build_publisher()
    await publish(publisher, buildings={"features": [_building(storeys_count=9)]})
    paths = handler.paths()
    broker = next(index for index, path in enumerate(paths) if "/api/broker/" in path)
    assert paths.index("/api/v1/scenarios/777/functional_zones") < broker
    assert paths.index("/api/v1/scenarios/777/buildings") < broker


@pytest.mark.asyncio
async def test_broker_message_lists_the_types_actually_written():
    publisher, handler, _ = build_publisher()
    await publish(publisher, buildings={"features": [_building("residential"), _building("business")]})
    assert handler.payload("scenario_objects_updated")["physical_object_types"] == [4, 5]


@pytest.mark.asyncio
async def test_generated_scenario_lands_in_a_service_project():
    """Владельца задаёт только `POST /projects?user_id=...` — иначе прогон повиснет у пользователя."""
    publisher, handler, _ = build_publisher()
    published = await publish(publisher)
    assert (published.project_id, published.scenario_id) == (900, 777)
    copy_body = handler.payload("/api/v1/scenarios/835")
    assert copy_body["project_id"] == 900
    assert copy_body["functional_zone_type_id"] == 13


@pytest.mark.asyncio
async def test_service_project_is_created_once_per_source_project():
    """Второй прогон переиспользует контейнер — на нём же сравниваются варианты профиля."""
    publisher, handler, _ = build_publisher()
    await publish(publisher)
    await publish(publisher)
    assert handler.paths().count("/api/v1/projects") == 1


@pytest.mark.asyncio
async def test_project_is_looked_up_once_per_run():
    publisher, _, reader = build_publisher()
    await publish(publisher, buildings={"features": [_building()]})
    assert reader.ref_calls == 1


@pytest.mark.asyncio
async def test_empty_scenario_is_not_announced():
    """Сообщение о пустом сценарии заставило бы сервисы посчитать пустоту."""
    publisher, handler, _ = build_publisher()
    published = await publish(publisher, zones={"features": []})
    assert published.notified is False
    assert not [path for path in handler.paths() if "/api/broker/" in path]


@pytest.mark.asyncio
async def test_zones_only_run_still_starts_the_scoring():
    """Застройка могла не получиться, но зоны сами по себе — повод считать оценки."""
    publisher, handler, _ = build_publisher()
    published = await publish(publisher, buildings=None)
    assert published.notified is True
    assert handler.paths().count("/api/broker/scenario_events/scenario_zones_updated") == 1
    assert not [path for path in handler.paths() if path.endswith("scenario_objects_updated")]


@pytest.mark.asyncio
async def test_project_without_region_fails_loudly():
    """`ProjectPost.territory_id` — обязательный «project region identifier»."""
    publisher, _, _ = build_publisher(reader=FakeUrbanReader(region_id=None))
    with pytest.raises(HTTPException) as exc_info:
        await publish(publisher)
    assert exc_info.value.status_code == 422


# --------------------------------------------------------------------- сервисная учётка


def test_service_account_id_comes_from_its_own_token():
    """`user_id` не настраивается отдельно: id учётки обязан совпасть с тем, чьим секретом получен токен."""
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "svc-42"}).encode()).decode().rstrip("=")
    assert _subject(f"header.{payload}.signature") == "svc-42"


def test_broken_token_does_not_crash_the_service():
    assert _subject("не-jwt") is None
    assert _subject("header.$$$.signature") is None
