"""Запись сгенерированного сценария в Urban API и объявление о нём в брокер.

Главное, что здесь проверяется, — порядок: сообщения в брокер уходят последними.
Сервисы оценок по сообщению идут читать сценарий, и опередить запись данных нельзя.
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
    {"functional_zone_type_id": 3, "name": "Жилая многоэтажная", "zone_nickname": "Ж-4"},
    {"functional_zone_type_id": 5, "name": "Рекреационная", "zone_nickname": None},
]
OBJECT_TYPES = [
    {"physical_object_type_id": 11, "name": "Здание"},
    {"physical_object_type_id": 12, "name": "Дорога"},
]


def _feature(zone_name: str | None = None, **properties: Any) -> dict[str, Any]:
    if zone_name is not None:
        properties["territory_zone_name"] = zone_name
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


class RoadsOnlyHandler(FakeHandler):
    """Справочник без единого подходящего типа под здание."""

    async def get(self, path: str, params=None, headers=None) -> Any:
        await super().get(path, params, headers)
        return [{"physical_object_type_id": 12, "name": "Дорога"}]


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
        self.geometry_calls = 0

    async def get_project_ref(self, scenario_id: int, token: str) -> tuple[int, int | None]:
        return 120, self.region_id

    async def get_project_geometry(self, project_id: int, token: str) -> dict[str, Any]:
        self.geometry_calls += 1
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
        profile_name="Многоэтажная жилая",
        year=2026,
        zones=zones if zones is not None else {"features": [_feature("Жилая многоэтажная")]},
        buildings=buildings,
    )


# --------------------------------------------------------------------- клиент записи


@pytest.mark.asyncio
async def test_zones_go_in_one_request_with_resolved_type_ids():
    """Ручка принимает массив — незачем слать по зоне за раз."""
    writer, handler = build_writer()
    written, unknown = await writer.add_functional_zones(
        7, [_feature("Жилая многоэтажная"), _feature("Рекреационная")], year=2026
    )
    assert (written, unknown) == (2, [])
    assert handler.paths().count("/api/v1/scenarios/7/functional_zones") == 1
    assert [zone["functional_zone_type_id"] for zone in handler.payload("/functional_zones")] == [3, 5]


@pytest.mark.asyncio
async def test_zone_type_is_matched_by_nickname_too():
    writer, handler = build_writer()
    written, unknown = await writer.add_functional_zones(7, [_feature("Ж-4")], year=2026)
    assert (written, unknown) == (1, [])
    assert handler.payload("/functional_zones")[0]["functional_zone_type_id"] == 3


@pytest.mark.asyncio
async def test_unknown_zone_names_are_reported_not_guessed():
    """`functional_zone_type_id` обязателен, а придумывать его нельзя."""
    writer, _ = build_writer()
    written, unknown = await writer.add_functional_zones(
        7, [_feature("Жилая многоэтажная"), _feature("Зона неизвестного назначения")], year=2026
    )
    assert written == 1
    assert unknown == ["Зона неизвестного назначения"]


@pytest.mark.asyncio
async def test_building_takes_two_requests_and_reuses_the_returned_id():
    writer, handler = build_writer()
    written = await writer.add_buildings(7, territory_id=42, features=[_feature(storeys_count=16)])
    assert written == 1
    assert handler.paths() == ["/api/v1/scenarios/7/physical_objects", "/api/v1/scenarios/7/buildings"]
    building = handler.payload("/buildings")
    assert building["physical_object_id"] == 555
    assert building["floors"] == 16
    assert building["is_scenario_object"] is True


@pytest.mark.asyncio
async def test_missing_building_type_names_what_is_available():
    """Молча выбрать «какой-нибудь» тип нельзя — здания уедут не туда."""
    handler = RoadsOnlyHandler()
    writer = UrbanScenarioWriter(handler, FakeTokens())
    with pytest.raises(HTTPException) as exc_info:
        await writer.building_type_id()
    assert exc_info.value.status_code == 502
    assert "дорога" in json.dumps(exc_info.value.detail, ensure_ascii=False).lower()


# --------------------------------------------------------------------- порядок публикации


@pytest.mark.asyncio
async def test_broker_is_notified_after_the_data_is_written():
    """Сервисы оценок по сообщению идут читать сценарий — опередить запись нельзя."""
    publisher, handler, _ = build_publisher()
    await publish(publisher, buildings={"features": [_feature(storeys_count=9)]})
    paths = handler.paths()
    broker = next(index for index, path in enumerate(paths) if "/api/broker/" in path)
    assert paths.index("/api/v1/scenarios/777/functional_zones") < broker
    assert paths.index("/api/v1/scenarios/777/buildings") < broker


@pytest.mark.asyncio
async def test_generated_scenario_lands_in_a_service_project():
    """Владельца задаёт только `POST /projects?user_id=...` — иначе прогон повиснет у пользователя."""
    publisher, handler, _ = build_publisher()
    published = await publish(publisher)
    assert published.project_id == 900
    assert published.scenario_id == 777
    copy_body = handler.payload("/api/v1/scenarios/835")
    assert copy_body["project_id"] == 900


@pytest.mark.asyncio
async def test_service_project_is_created_once_per_source_project():
    """Второй прогон переиспользует контейнер — на нём же сравниваются варианты профиля."""
    publisher, handler, _ = build_publisher()
    await publish(publisher)
    await publish(publisher)
    assert handler.paths().count("/api/v1/projects") == 1


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
