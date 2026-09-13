"""Публикация результата: сценарий в Urban API плюс объявление в брокер.

Порядок обязателен и не переставляется: сначала данные, потом сообщения. Сервисы
оценок по сообщению идут читать сценарий, и если зоны с застройкой ещё не легли,
они посчитают пустоту.

Читаем исходный проект токеном пользователя (свой проект видит только он), пишем —
сервисным. Это единственное место в сервисе, где в одном действии участвуют оба.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import HTTPException
from loguru import logger

from app.clients.urban_api_client import UrbanApiClient
from app.clients.urban_scenario_writer import BuildingsWritten, UrbanScenarioWriter
from app.common.exceptions.http_exception import http_exception


@dataclass
class PublishedScenario:
    """Что получилось записать. `scenario_id` — тот, по которому придут оценки.

    `failed_stage` is set when the scenario was created but a later step failed:
    the scenario exists in Urban API and must stay findable by its id.
    """

    project_id: int
    scenario_id: int
    zones_written: int = 0
    buildings_written: int = 0
    buildings_failed: int = 0
    buildings_total: int = 0
    services_written: int = 0
    unknown_zone_names: list[str] = field(default_factory=list)
    unknown_service_names: list[str] = field(default_factory=list)
    notified: bool = False
    failed_stage: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScenarioPublisher:
    def __init__(
        self,
        urban_client: UrbanApiClient,
        writer: UrbanScenarioWriter,
        project_prefix: str = "BuildPlanner",
    ):
        self._urban = urban_client
        self._writer = writer
        self._prefix = project_prefix
        # Проект-контейнер на каждый исходный проект: создаётся один раз, дальше в него
        # копируются сценарии всех прогонов. Это же делает дешёвым повторный прогон
        # с другим профилем — сравнивать варианты можно в одном месте.
        self._service_projects: dict[int, int] = {}

    async def publish(
        self,
        *,
        source_scenario_id: int,
        user_token: str,
        profile_id: int | None,
        profile_name: str,
        year: int,
        zones: dict[str, Any] | None,
        buildings: dict[str, Any] | None,
    ) -> PublishedScenario:
        source_project_id, region_id = await self._urban.get_project_ref(source_scenario_id, user_token)
        project_id = await self._service_project(source_project_id, region_id, user_token)
        scenario_id = await self._writer.copy_scenario(
            source_scenario_id=source_scenario_id,
            project_id=project_id,
            name=f"{profile_name} — прогон по сценарию {source_scenario_id}",
            profile_id=profile_id,
        )
        published = PublishedScenario(project_id=project_id, scenario_id=scenario_id)

        stage = "functional_zones"
        try:
            await self._add_zones(published, zones, year)
            stage = "buildings"
            written = await self._add_buildings(published, region_id, buildings)
            stage = "broker"
            await self._notify(published, written)
        except HTTPException as exc:
            logger.warning("Scenario {} is written partially, failed at {}: {}", scenario_id, stage, exc.detail)
            published.failed_stage = stage
            published.error = str(exc.detail)[:500]
        return published

    async def _add_zones(self, published: PublishedScenario, zones: dict[str, Any] | None, year: int) -> None:
        zone_features = (zones or {}).get("features") or []
        if not zone_features:
            return
        published.zones_written, published.unknown_zone_names = await self._writer.add_functional_zones(
            published.scenario_id, zone_features, year=year
        )
        if published.unknown_zone_names:
            logger.warning("Зоны без типа в справочнике Urban API: {}", published.unknown_zone_names)

    async def _add_buildings(
        self,
        published: PublishedScenario,
        region_id: int | None,
        buildings: dict[str, Any] | None,
    ) -> BuildingsWritten:
        building_features = (buildings or {}).get("features") or []
        published.buildings_total = len(building_features)
        if not building_features or region_id is None:
            return BuildingsWritten()

        written = await self._writer.add_buildings(published.scenario_id, region_id, building_features)
        published.buildings_written = written.written
        published.buildings_failed = written.failed
        published.services_written = written.services_written
        published.unknown_service_names = written.unknown_service_names
        return written

    async def _notify(self, published: PublishedScenario, written: BuildingsWritten) -> None:
        """Сообщения в брокер — последним шагом и только по тому, что реально записано."""
        if published.zones_written:
            await self._writer.notify_zones_updated(published.project_id, published.scenario_id)
        if published.buildings_written:
            await self._writer.notify_objects_updated(
                published.project_id,
                published.scenario_id,
                physical_object_types=written.object_type_ids,
                service_types=written.service_type_ids,
            )
        published.notified = bool(published.zones_written or published.buildings_written)
        if not published.notified:
            logger.warning("Сценарий {} пуст — в брокер не сообщаю", published.scenario_id)

    async def _service_project(self, source_project_id: int, region_id: int | None, user_token: str) -> int:
        cached = self._service_projects.get(source_project_id)
        if cached is not None:
            return cached

        if region_id is None:
            raise http_exception(
                422,
                "У проекта не определён регион — создать сервисный проект нельзя",
                _input={"project_id": source_project_id},
            )
        geometry = await self._urban.get_project_geometry(source_project_id, user_token)
        if geometry is None:
            raise http_exception(
                422,
                "У проекта не удалось получить границу",
                _input={"project_id": source_project_id},
            )

        project_id = await self._writer.create_project(
            name=f"{self._prefix}: проект {source_project_id}",
            territory_id=region_id,
            geometry=geometry,
            description=(
                f"Служебный проект BuildPlanner для расчёта оценок по генерациям " f"проекта {source_project_id}."
            ),
        )
        self._service_projects[source_project_id] = project_id
        return project_id
