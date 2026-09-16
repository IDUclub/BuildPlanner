"""Запись сгенерированного сценария в Urban API под сервисной учёткой.

Зачем отдельный клиент: `UrbanApiClient` только читает, и это его ценное свойство —
на него смотрят, чтобы убедиться, что пайплайн ничего не портит. Всё, что меняет
чужие данные, собрано здесь.

Почему под сервисной учёткой. Владельца можно задать ровно в одном месте API —
`POST /api/v1/projects?user_id=...`. У `Copy Scenario` владельца нет: сценарий
наследует проект, а проект — своего хозяина. Поэтому генерация складывается
в проект-контейнер сервисной учётки, и в проекте пользователя не появляется.

Оценки считают сторонние сервисы, подписанные на Kafka. Своего продюсера мы не
держим: у Urban API есть HTTP-фасад над брокером (`/api/broker/...`), которым
и пользуемся.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from fastapi import HTTPException
from loguru import logger

from app.clients.genbuilder_client import building_services
from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.auth.service_token import ServiceTokenProvider
from app.common.constants.pipeline_constants import (
    BUILDING_TYPE_NAME_BY_ZONE,
    DEFAULT_BUILDING_TYPE_NAME,
    RESIDENTIAL_ZONE,
    ZONE_NAME_TO_PROFILE,
)
from app.common.exceptions.http_exception import http_exception

CATALOGUE_TTL_SECONDS = 3600


@dataclass
class BuildingsWritten:
    """What `add_buildings` managed to write. Type lists cover written buildings only."""

    written: int = 0
    failed: int = 0
    living_written: int = 0
    services_written: int = 0
    services_failed: int = 0
    object_type_ids: list[int] = field(default_factory=list)
    service_type_ids: list[int] = field(default_factory=list)
    unknown_service_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Service:
    type_id: int
    name: str
    capacity: int | None


@dataclass(frozen=True)
class _BuildingOutcome:
    """A building that is written; its services may be written only partly."""

    type_id: int
    service_type_ids: list[int]
    services_failed: int


class UrbanScenarioWriter:
    def __init__(
        self,
        handler: AsyncJsonApiHandler,
        token_provider: ServiceTokenProvider,
        zone_source: str = "BuildPlanner",
        max_concurrency: int = 8,
    ):
        self._api = handler
        self._tokens = token_provider
        self._zone_source = zone_source
        self._semaphore = asyncio.Semaphore(max(max_concurrency, 1))
        self._zone_types: tuple[float, dict[str, int], set[int]] | None = None
        self._object_types: tuple[float, dict[str, int]] | None = None
        self._service_types: tuple[float, dict[str, int]] | None = None

    # ------------------------------------------------------------------ справочники

    async def zone_types(self) -> tuple[dict[str, int], set[int]]:
        """Справочник типов зон: `имя -> id` и множество известных id.

        Имена в нём — английские слаги (`residential_multistorey`), русское название
        лежит в `zone_nickname`, а GenPlanner отдаёт своё («жилая многоэтажная»).
        Поэтому сверка по именам здесь запасная: основной путь — целое
        `properties.territory_zone`, см. `add_functional_zones`.
        """
        cached = self._zone_types
        if cached and time.monotonic() - cached[0] < CATALOGUE_TTL_SECONDS:
            return cached[1], cached[2]

        rows = await self._get("/api/v1/functional_zones_types")
        by_name: dict[str, int] = {}
        known: set[int] = set()
        for row in rows if isinstance(rows, list) else []:
            type_id = row.get("functional_zone_type_id")
            if not isinstance(type_id, int):
                continue
            known.add(type_id)
            for key in (row.get("name"), row.get("zone_nickname")):
                if isinstance(key, str) and key.strip():
                    by_name.setdefault(_normalize(key), type_id)
        self._zone_types = (time.monotonic(), by_name, known)
        return by_name, known

    async def object_type_ids(self) -> dict[str, int]:
        self._object_types = await self._name_catalogue(
            self._object_types, "/api/v1/physical_object_types", "physical_object_type_id"
        )
        return self._object_types[1]

    async def service_type_ids(self) -> dict[str, int]:
        """GenBuilder names services after this catalogue: it takes them from the territory normatives."""
        self._service_types = await self._name_catalogue(
            self._service_types, "/api/v1/service_types", "service_type_id"
        )
        return self._service_types[1]

    async def building_type_id(self, zone: str | None = None) -> int:
        """Тип физобъекта под здание: жилое и нежилое — разные записи справочника."""
        wanted = BUILDING_TYPE_NAME_BY_ZONE.get(str(zone or ""), DEFAULT_BUILDING_TYPE_NAME)
        mapping = await self.object_type_ids()
        type_id = mapping.get(_normalize(wanted))
        if type_id is None:
            raise http_exception(
                502,
                "В справочнике Urban API нет типа физобъекта под здание",
                _input={"искали": wanted, "зона": zone},
                _detail={"доступно": sorted(mapping)[:50]},
            )
        return type_id

    async def _name_catalogue(
        self,
        cached: tuple[float, dict[str, int]] | None,
        path: str,
        id_key: str,
    ) -> tuple[float, dict[str, int]]:
        if cached and time.monotonic() - cached[0] < CATALOGUE_TTL_SECONDS:
            return cached

        rows = await self._get(path)
        mapping = {
            _normalize(row["name"]): row[id_key]
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row.get("name"), str) and isinstance(row.get(id_key), int)
        }
        return time.monotonic(), mapping

    # ------------------------------------------------------------------ проект и сценарий

    async def create_project(
        self,
        name: str,
        territory_id: int,
        geometry: dict[str, Any],
        description: str | None = None,
    ) -> int:
        """Проект-контейнер сервисной учётки. `user_id` — `sub` её собственного токена."""
        user_id = await self._tokens.get_user_id()
        created = await self._post(
            "/api/v1/projects",
            {
                "name": name,
                "territory_id": territory_id,
                "description": description,
                "public": False,
                "is_regional": False,
                "is_city": False,
                "territory": {"geometry": geometry},
                "properties": {"generated_by": "buildplanner"},
            },
            params={"user_id": user_id},
        )
        project_id = (created or {}).get("project_id")
        if not isinstance(project_id, int):
            raise http_exception(502, "Urban API не вернул project_id", _detail=created)
        logger.info("Создан сервисный проект {} под учёткой {}", project_id, user_id)
        return project_id

    async def copy_scenario(
        self,
        source_scenario_id: int,
        project_id: int,
        name: str,
        profile_id: int | None = None,
    ) -> int:
        """`POST /api/v1/scenarios/{id}` — копия исходного сценария в наш проект.

        `functional_zone_type_id` в теле подписан как «target profile identifier
        for the scenario», и это то же пространство id, что и профиль пайплайна.
        """
        created = await self._post(
            f"/api/v1/scenarios/{source_scenario_id}",
            {
                "project_id": project_id,
                "functional_zone_type_id": profile_id,
                "name": name,
                "properties": {"generated_by": "buildplanner", "source_scenario_id": source_scenario_id},
            },
        )
        scenario_id = (created or {}).get("scenario_id")
        if not isinstance(scenario_id, int):
            raise http_exception(502, "Urban API не вернул scenario_id", _detail=created)
        return scenario_id

    # ------------------------------------------------------------------ содержимое сценария

    async def add_functional_zones(
        self,
        scenario_id: int,
        features: Iterable[dict[str, Any]],
        year: int,
    ) -> tuple[int, list[str]]:
        """Все зоны уходят одним запросом: ручка принимает массив.

        `properties.territory_zone` GenPlanner'а — то же число, что и
        `functional_zone_type_id` Urban API (1 residential, 2 recreation, 7 business,
        13 residential_multistorey), поэтому переводить его не нужно: достаточно
        убедиться, что стенд такой id знает.

        Возвращает число записанных зон и описания пропущенных: тип обязателен,
        и подставлять его наугад нельзя.
        """
        by_name, known = await self.zone_types()
        payload: list[dict[str, Any]] = []
        skipped: set[str] = set()

        for feature in features:
            properties = feature.get("properties") or {}
            type_id = _zone_type_id(properties, by_name, known)
            if type_id is None:
                skipped.add(str(properties.get("territory_zone_name") or properties.get("territory_zone")))
                continue
            payload.append(
                {
                    "geometry": feature.get("geometry"),
                    "functional_zone_type_id": type_id,
                    "name": properties.get("territory_zone_name"),
                    "year": year,
                    "source": self._zone_source,
                    "properties": {"generated_by": "buildplanner"},
                }
            )

        if not payload:
            return 0, sorted(skipped)
        written = await self._post(f"/api/v1/scenarios/{scenario_id}/functional_zones", payload)
        return len(written if isinstance(written, list) else payload), sorted(skipped)

    async def add_buildings(
        self,
        scenario_id: int,
        territory_id: int,
        features: Sequence[dict[str, Any]],
    ) -> BuildingsWritten:
        """Здание — это два запроса: физобъект с геометрией, затем строение на него;
        сервис GenBuilder — ещё по запросу на каждый сервис в здании.

        Массовой ручки у Urban API нет, поэтому шлём параллельно с ограничением:
        реальный прогон — это тысячи зданий, и пускать их без предела нельзя.

        `living_written` считается отдельно: жилыми дома делает тип физобъекта, и по нему же
        их ищет SIRTEP — без них он очередь строительства не построит.

        Сервис с именем, которого нет в справочнике, не пишется, но здание остаётся:
        подставлять тип сервиса наугад нельзя. Сервис, который Urban API не принял,
        тоже не отменяет здание: физобъект и строение к этому моменту уже записаны.
        """
        type_ids = {kind: await self.building_type_id(kind) for kind in {_building_kind(f) for f in features}}
        catalogue = await self.service_type_ids() if any(building_services(f) for f in features) else {}

        unknown: set[str] = set()
        jobs = []
        for feature in features:
            services, missing = _resolve_services(feature, catalogue)
            unknown.update(missing)
            jobs.append(
                self._add_building(scenario_id, territory_id, type_ids[_building_kind(feature)], feature, services)
            )
        results = await asyncio.gather(*jobs, return_exceptions=True)

        written = [result for result in results if not isinstance(result, BaseException)]
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            logger.warning("Не записано зданий: {} из {}. Первая ошибка: {}", len(failures), len(results), failures[0])
        if unknown:
            logger.warning("Service types unknown to Urban API, services skipped: {}", sorted(unknown))
        living_type_id = type_ids.get(RESIDENTIAL_ZONE)
        return BuildingsWritten(
            written=len(written),
            failed=len(failures),
            living_written=sum(1 for outcome in written if outcome.type_id == living_type_id),
            services_written=sum(len(outcome.service_type_ids) for outcome in written),
            services_failed=sum(outcome.services_failed for outcome in written),
            object_type_ids=sorted({outcome.type_id for outcome in written}),
            service_type_ids=sorted({type_id for outcome in written for type_id in outcome.service_type_ids}),
            unknown_service_names=sorted(unknown),
        )

    async def _add_building(
        self,
        scenario_id: int,
        territory_id: int,
        type_id: int,
        feature: dict[str, Any],
        services: Sequence[_Service],
    ) -> _BuildingOutcome:
        properties = feature.get("properties") or {}
        async with self._semaphore:
            urban_object = await self._post(
                f"/api/v1/scenarios/{scenario_id}/physical_objects",
                {
                    "geometry": feature.get("geometry"),
                    "territory_id": territory_id,
                    "physical_object_type_id": type_id,
                    "name": properties.get("name"),
                    "properties": {"generated_by": "buildplanner"},
                },
            )
            physical_object_id = ((urban_object or {}).get("physical_object") or {}).get("physical_object_id")
            if not isinstance(physical_object_id, int):
                raise http_exception(502, "Urban API не вернул physical_object_id", _detail=urban_object)

            await self._post(
                f"/api/v1/scenarios/{scenario_id}/buildings",
                {
                    "physical_object_id": physical_object_id,
                    "floors": _as_int(properties.get("floors_count")),
                    "building_area_modeled": _as_float(properties.get("building_area")),
                    "is_scenario_object": True,
                    "properties": {"generated_by": "buildplanner"},
                },
            )
            service_type_ids: list[int] = []
            services_failed = 0
            if services:
                service_type_ids, services_failed = await self._add_services(
                    scenario_id, physical_object_id, urban_object, services
                )
        return _BuildingOutcome(type_id, service_type_ids, services_failed)

    async def _add_services(
        self,
        scenario_id: int,
        physical_object_id: int,
        urban_object: dict[str, Any],
        services: Sequence[_Service],
    ) -> tuple[list[int], int]:
        """Service types written and the number of services lost.

        The building is already in the scenario by now, so a lost service is counted, not raised:
        raising would report the building as failed and drop its type from the broker message.
        """
        geometry_id = (urban_object.get("object_geometry") or {}).get("object_geometry_id")
        if not isinstance(geometry_id, int):
            logger.warning(
                "Urban API returned no object_geometry_id for physical object {}, services skipped: {}",
                physical_object_id,
                [service.name for service in services],
            )
            return [], len(services)

        written: list[int] = []
        for service in services:
            try:
                await self._post(
                    f"/api/v1/scenarios/{scenario_id}/services",
                    {
                        "physical_object_id": physical_object_id,
                        "is_scenario_physical_object": True,
                        "object_geometry_id": geometry_id,
                        "is_scenario_geometry": True,
                        "service_type_id": service.type_id,
                        "name": service.name,
                        "capacity": service.capacity,
                        "is_capacity_real": False,
                        "properties": {"generated_by": "buildplanner"},
                    },
                )
            except HTTPException as exc:
                logger.warning(
                    "Service {} of physical object {} is not written: {}", service.name, physical_object_id, exc.detail
                )
                continue
            written.append(service.type_id)
        return written, len(services) - len(written)

    # ------------------------------------------------------------------ брокер

    async def notify_zones_updated(self, project_id: int, scenario_id: int) -> None:
        await self._post(
            "/api/broker/scenario_events/scenario_zones_updated",
            {"project_id": project_id, "scenario_id": scenario_id},
        )

    async def notify_objects_updated(
        self,
        project_id: int,
        scenario_id: int,
        physical_object_types: Sequence[int],
        service_types: Sequence[int] = (),
    ) -> None:
        await self._post(
            "/api/broker/scenario_events/scenario_objects_updated",
            {
                "project_id": project_id,
                "scenario_id": scenario_id,
                "service_types": list(service_types),
                "physical_object_types": list(physical_object_types),
            },
        )

    # ------------------------------------------------------------------ транспорт

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.get_token()}"}

    async def _get(self, path: str) -> Any:
        return await self._api.get(path, headers=await self._headers())

    async def _post(self, path: str, json_data: Any, params: dict[str, Any] | None = None) -> Any:
        return await self._api.post(path, json_data=json_data, params=params, headers=await self._headers())


def _zone_type_id(properties: dict[str, Any], by_name: dict[str, int], known: set[int]) -> int | None:
    """Сначала число, потом имя. Незнакомый стенду id наугад не подставляем."""
    raw = properties.get("territory_zone")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in known:
        return raw

    name = properties.get("territory_zone_name")
    if not isinstance(name, str):
        return None
    from_catalogue = by_name.get(_normalize(name))
    if from_catalogue is not None:
        return from_catalogue
    # Имён GenPlanner'а в справочнике нет — переводим своей таблицей.
    from_ours = ZONE_NAME_TO_PROFILE.get(_normalize(name))
    return from_ours if from_ours in known else None


def _zone_of(feature: dict[str, Any]) -> str:
    return str((feature.get("properties") or {}).get("zone") or "")


def _building_kind(feature: dict[str, Any]) -> str | None:
    """GenBuilder puts service buildings into the residential zone, but they are not dwellings."""
    return None if building_services(feature) else _zone_of(feature)


def _resolve_services(feature: dict[str, Any], catalogue: dict[str, int]) -> tuple[list[_Service], list[str]]:
    resolved: list[_Service] = []
    missing: list[str] = []
    for name, capacity in building_services(feature):
        type_id = catalogue.get(_normalize(name))
        if type_id is None:
            missing.append(name)
            continue
        value = _as_float(capacity)
        resolved.append(_Service(type_id=type_id, name=name, capacity=round(value) if value is not None else None))
    return resolved, missing


def _normalize(name: Any) -> str:
    """Имена приходят из разных систем; сверяем мягко — регистр и «ё» не считаются."""
    return str(name).strip().casefold().replace("ё", "е") if name is not None else ""


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
