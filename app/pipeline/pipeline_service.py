"""Оркестрация пайплайна: показатели -> профиль -> зоны -> застройка.

Единственная реализация — асинхронный генератор событий `stream()`. Синхронный `run()`
просто вычерпывает его до конца, поэтому REST и чат гарантированно ходят одним путём
и не расходятся в поведении.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import HTTPException
from loguru import logger

from app.clients.genbuilder_client import GenBuilderClient
from app.clients.genplanner_client import GenPlannerClient
from app.clients.urban_api_client import UrbanApiClient
from app.common.constants.pipeline_constants import PROFILE_NAMES, RESIDENTIAL_ZONE, SELECTION_INDICATOR_IDS
from app.common.exceptions.http_exception import http_exception
from app.pipeline import events
from app.pipeline.buildings_summary import summarize_buildings
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.indicators_view import build_overview
from app.pipeline.profile_selector import NoIndicatorValuesError, ProfileSelection, select_profile
from app.pipeline.scenario_publisher import BROKER_STAGE, OBJECTS_UPDATED_EVENT, ZONES_UPDATED_EVENT, ScenarioPublisher
from app.pipeline.schema.pipeline_schema import PipelineResultSchema, ProfileSelectionSchema
from app.pipeline.targets_policy import build_targets_by_zone, to_genbuilder_targets, zones_without_volume_target
from app.pipeline.zone_mapper import MappingResult, map_zones_to_blocks


@dataclass
class PipelineRun:
    """Состояние одного прогона: наполняется по ходу потока событий."""

    scenario_id: int
    selection: ProfileSelection | None = None
    indicators_overview: dict[str, Any] | None = None
    zones: dict[str, Any] | None = None
    roads: dict[str, Any] | None = None
    buildings: dict[str, Any] | None = None
    published: dict[str, Any] | None = None
    source_project: tuple[int, int | None] | None = None
    targets_by_zone: dict[str, dict[str, Any]] = field(default_factory=dict)
    mapping_summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failure: HTTPException | None = None


class PipelineService:
    def __init__(
        self,
        urban_client: UrbanApiClient,
        genplanner_client: GenPlannerClient,
        genbuilder_client: GenBuilderClient,
        cache_ttl_seconds: int = 3600,
        publisher: ScenarioPublisher | None = None,
    ):
        self._urban = urban_client
        self._genplanner = genplanner_client
        self._genbuilder = genbuilder_client
        self._cache_ttl = cache_ttl_seconds
        self._publisher = publisher
        self._zones_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ публичный API

    async def run(self, scenario_id: int, token: str, options: PipelineOptionsDTO) -> PipelineResultSchema:
        """Синхронный прогон. Ошибки поднимаются как HTTP, а не прячутся в поток."""
        run = PipelineRun(scenario_id=scenario_id)
        async for _ in self.stream(scenario_id, token, options, run):
            pass
        if run.failure is not None:
            raise run.failure
        assert run.selection is not None and run.zones is not None  # гарантировано отсутствием failure
        return PipelineResultSchema(
            scenario_id=scenario_id,
            profile=ProfileSelectionSchema(**run.selection.as_dict()),
            indicators_overview=run.indicators_overview,
            zones=run.zones,
            roads=run.roads,
            buildings=run.buildings,
            targets_by_zone=run.targets_by_zone,
            mapping_summary=run.mapping_summary,
            published=run.published,
            warnings=run.warnings,
        )

    async def stream(
        self,
        scenario_id: int,
        token: str,
        options: PipelineOptionsDTO,
        run: PipelineRun | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Основной путь. Фатальная ошибка приходит событием `error`, а не разрывом потока."""
        run = run or PipelineRun(scenario_id=scenario_id)
        try:
            async for event in self._execute(run, token, options):
                yield event
            # Публикация снаружи `_execute` намеренно: тот выходит раньше времени в
            # нескольких местах (застройка пропущена, застраивать нечего, GenBuilder упал),
            # а сохранить зоны и запустить расчёт оценок стоит в любом из этих случаев.
            async for event in self._emit_published(run, token, options):
                yield event
        except HTTPException as exc:
            run.failure = exc
            detail = exc.detail if isinstance(exc.detail, dict) else {"msg": str(exc.detail)}
            logger.error("Пайплайн сценария {} упал: {}", scenario_id, detail)
            yield events.error(stage=detail.get("stage", "pipeline"), detail=json.dumps(detail, ensure_ascii=False))

    async def territory_indicators(self, scenario_id: int, token: str) -> tuple[dict[str, Any] | None, str | None]:
        """Витрина показателей проекта. Никогда не роняет прогон — это справочная часть ответа.

        Запрос отдельный от выбора профиля: тот тянет ровно десять индикаторов и обязан быть
        быстрым и предсказуемым, а этот забирает у сценария всё, что есть.
        """
        try:
            raw_values = await self._urban.get_scenario_indicators(scenario_id, token)
            rows = self._urban.latest_rows_by_indicator(raw_values)
            logger.info("У сценария {}: {} значений, {} показателей", scenario_id, len(raw_values), len(rows))
            groups = await self._urban.get_indicator_groups(token)
            return build_overview(raw_values, groups, rows), None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Не удалось собрать показатели сценария {}: {}", scenario_id, exc)
            return None, str(exc)[:500]

    # ------------------------------------------------------------------ шаги пайплайна

    async def _execute(
        self,
        run: PipelineRun,
        token: str,
        options: PipelineOptionsDTO,
    ) -> AsyncIterator[dict[str, Any]]:
        selection = await self._resolve_profile(run, token, options)
        yield events.progress(events.STAGE_FETCH_INDICATORS)
        yield events.indicators([entry.as_dict() for entry in selection.scoreboard])

        async for event in self._emit_territory_indicators(run, token):
            yield event

        yield events.progress(events.STAGE_SELECT_PROFILE, selection.reason)
        yield events.profile_selected(selection.as_dict())

        if not selection.buildable:
            message = (
                f"Профиль «{selection.profile_name}» не относится к застраиваемым: "
                "GenBuilder такие зоны исключает. Сгенерирую зоны и застрою только ту часть, "
                "которая застраивается."
            )
            run.warnings.append(message)
            yield events.warning(events.STAGE_SELECT_PROFILE, "non_buildable_profile", message)

        # --- зоны
        yield events.progress(events.STAGE_GENPLANNER)
        generated = await self._generate_zones(run, token, options, selection)
        run.zones = generated.get("zones") or {"type": "FeatureCollection", "features": []}
        run.roads = generated.get("roads")
        yield events.zones(run.zones)
        if run.roads:
            yield events.roads(run.roads)

        # --- блоки
        yield events.progress(events.STAGE_MAP_ZONES)
        mapping = map_zones_to_blocks(run.zones)
        run.mapping_summary = mapping.summary()
        mapping_warning = mapping.warning_message()
        if mapping_warning:
            run.warnings.append(mapping_warning)
            yield events.warning(events.STAGE_MAP_ZONES, "zones_skipped", mapping_warning)

        if options.skip_generation:
            yield events.progress(events.STAGE_ASSEMBLE, "Застройка пропущена по запросу")
            return

        if mapping.buildable_count == 0:
            message = "Ни одна из сгенерированных зон не застраивается — застройку не запускаю."
            run.warnings.append(message)
            yield events.warning(events.STAGE_GENBUILDER, "nothing_to_build", message)
            return

        async for event in self._emit_buildings(run, token, options, selection, mapping):
            yield event

    # ------------------------------------------------------------------ внутренности

    async def _emit_buildings(
        self,
        run: PipelineRun,
        token: str,
        options: PipelineOptionsDTO,
        selection: ProfileSelection,
        mapping: MappingResult,
    ) -> AsyncIterator[dict[str, Any]]:
        """Volume targets, the services region and GenBuilder; a failed build keeps the zones."""
        # Цели объёма считает политика: у пользователя их не спрашиваем, число жителей
        # выводится из площади блоков и нормативного потолка плотности.
        run.targets_by_zone = build_targets_by_zone(
            selection.profile_id,
            mapping.zones_present,
            mapping.area_by_zone,
            options.targets_overrides,
        )
        idle_zones = zones_without_volume_target(run.targets_by_zone)
        if idle_zones:
            message = (
                f"У зон {', '.join(idle_zones)} нет цели объёма (residents/coverage_area) — "
                "GenBuilder их не застроит."
            )
            run.warnings.append(message)
            yield events.warning(events.STAGE_GENBUILDER, "no_volume_target", message)

        region_id = await self._services_region(run, token)
        if region_id is None and RESIDENTIAL_ZONE in mapping.zones_present:
            message = (
                "Регион проекта не определён — сервисы (школы, детские сады и т. п.) "
                "в жилых кварталах расставлены не будут."
            )
            run.warnings.append(message)
            yield events.warning(events.STAGE_GENBUILDER, "services_region_unknown", message)

        yield events.progress(events.STAGE_GENBUILDER)
        try:
            built = await self._genbuilder.generate_by_territory(
                token=token,
                blocks=mapping.blocks,
                targets_by_zone=to_genbuilder_targets(run.targets_by_zone),
                territory_id=region_id,
            )
        except HTTPException as exc:
            # Зоны уже получены и отданы — это полезный частичный результат, а не провал прогона.
            message = "Не удалось сгенерировать застройку, зоны остаются в силе."
            run.warnings.append(message)
            logger.warning("GenBuilder упал: {}", exc.detail)
            yield events.warning(events.STAGE_GENBUILDER, str(exc.detail)[:500], message)
            return

        run.buildings = built.get("buildings") or built.get("content") or built
        yield events.progress(events.STAGE_ASSEMBLE)
        yield events.result(run.buildings, self._build_summary(run, built))

        for descriptor in built.get("files", []) or []:
            yield {"type": "file", **descriptor, "source_service": descriptor.get("source_service", "genbuilder")}

    async def _emit_published(
        self,
        run: PipelineRun,
        token: str,
        options: PipelineOptionsDTO,
    ) -> AsyncIterator[dict[str, Any]]:
        """Сохранение результата под сервисной учёткой и объявление в брокер.

        Не фатально: сгенерированные зоны и застройка уже у пользователя, и потерять
        их из-за недоступного Urban API было бы хуже, чем остаться без оценок.
        """
        if self._publisher is None or not options.publish or run.zones is None or run.selection is None:
            return

        yield events.progress(events.STAGE_PUBLISH)
        try:
            published = await self._publisher.publish(
                source_scenario_id=run.scenario_id,
                user_token=token,
                profile_id=run.selection.profile_id,
                profile_name=run.selection.profile_name,
                year=datetime.now(timezone.utc).year,
                zones=run.zones,
                buildings=run.buildings,
            )
        except HTTPException as exc:
            message = "Сценарий не сохранён в Urban API — оценки по нему не посчитаются."
            run.warnings.append(message)
            logger.warning("Публикация сценария {} не удалась: {}", run.scenario_id, exc.detail)
            yield events.warning(events.STAGE_PUBLISH, str(exc.detail)[:500], message)
            return

        run.published = published.as_dict()
        yield events.scenario_published(run.published)
        for message, detail in _publication_warnings(run.published):
            run.warnings.append(message)
            yield events.warning(events.STAGE_PUBLISH, detail, message)

    async def _emit_territory_indicators(self, run: PipelineRun, token: str) -> AsyncIterator[dict[str, Any]]:
        overview, error = await self.territory_indicators(run.scenario_id, token)
        run.indicators_overview = overview
        if overview is not None:
            yield events.territory_indicators(overview)
        elif error:
            yield events.warning(events.STAGE_FETCH_INDICATORS, error, "Показатели проекта недоступны.")

    async def _resolve_profile(
        self,
        run: PipelineRun,
        token: str,
        options: PipelineOptionsDTO,
    ) -> ProfileSelection:
        if options.profile_id is not None:
            selection = ProfileSelection(
                profile_id=options.profile_id,
                profile_name=PROFILE_NAMES[options.profile_id],
                indicator_id=0,
                reason="Профиль задан вручную, показатели сценария не использовались.",
            )
            run.selection = selection
            return selection

        raw_values = await self._urban.get_scenario_indicators(run.scenario_id, token, SELECTION_INDICATOR_IDS)
        values = self._urban.latest_values_by_indicator(raw_values, SELECTION_INDICATOR_IDS)
        try:
            selection = select_profile(values)
        except NoIndicatorValuesError as exc:
            raise http_exception(
                422,
                str(exc),
                _input={"scenario_id": run.scenario_id},
                _detail={"stage": events.STAGE_FETCH_INDICATORS, "expected": list(SELECTION_INDICATOR_IDS)},
            ) from exc
        run.selection = selection
        return selection

    async def _generate_zones(
        self,
        run: PipelineRun,
        token: str,
        options: PipelineOptionsDTO,
        selection: ProfileSelection,
    ) -> dict[str, Any]:
        balance = options.territory_balance or await self._genplanner.get_default_func_ratio(selection.profile_id)
        cache_key = self._cache_key(run.scenario_id, selection.profile_id, balance, options)

        cached = self._zones_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            logger.info("Зоны сценария {} взяты из кэша", run.scenario_id)
            return cached[1]

        project_id = options.project_id or (await self._source_project(run, token))[0]
        generated = await self._genplanner.run_func_generation(
            token=token,
            scenario_id=run.scenario_id,
            project_id=project_id,
            territory_balance=balance,
            test=options.test,
        )
        self._zones_cache[cache_key] = (time.monotonic(), generated)
        return generated

    async def _source_project(self, run: PipelineRun, token: str) -> tuple[int, int | None]:
        """`(project_id, region_id)` of the source scenario, fetched once per run."""
        if run.source_project is None:
            run.source_project = await self._urban.get_project_ref(run.scenario_id, token)
        return run.source_project

    async def _services_region(self, run: PipelineRun, token: str) -> int | None:
        """Region whose normatives let GenBuilder place services; an unknown region only costs the services."""
        try:
            _, region_id = await self._source_project(run, token)
        except HTTPException as exc:
            logger.warning("Project region of scenario {} is unavailable: {}", run.scenario_id, exc.detail)
            return None
        return region_id

    @staticmethod
    def _cache_key(
        scenario_id: int,
        profile_id: int,
        balance: dict[str, float],
        options: PipelineOptionsDTO,
    ) -> str:
        """Всё, что меняет запрос к GenPlanner, обязано попасть в ключ — иначе отдадим чужие зоны."""
        payload = json.dumps(
            {"balance": balance, "project_id": options.project_id, "test": options.test},
            sort_keys=True,
        ).encode("utf-8")
        return f"{scenario_id}:{profile_id}:{hashlib.sha256(payload).hexdigest()[:16]}"

    @staticmethod
    def _build_summary(run: PipelineRun, built: dict[str, Any]) -> dict[str, Any]:
        features = (run.buildings or {}).get("features") or []
        summary = {**summarize_buildings(features), **(built.get("summary") or {})}
        summary["profile"] = run.selection.profile_name if run.selection else None
        summary["blocks"] = run.mapping_summary
        return summary


# What a broker message starts scoring for, in the words of the warning.
SCORING_SCOPE: dict[str, str] = {
    ZONES_UPDATED_EVENT: "функциональным зонам",
    OBJECTS_UPDATED_EVENT: "зданиям и сервисам",
}


def _publication_warnings(published: dict[str, Any]) -> list[tuple[str, str]]:
    """Everything the user must know about a scenario that was saved only partly, as `(message, detail)`."""
    warnings: list[tuple[str, str]] = []
    if published.get("failed_stage") == BROKER_STAGE:
        # The data is in Urban API, only the announcement is missing: «written partly»
        # would send the user looking for losses that are not there.
        warnings.append((_broker_failure_message(published), str(published.get("error") or BROKER_STAGE)))
    elif published.get("failed_stage"):
        warnings.append(
            (
                f"Сценарий {published.get('scenario_id')} создан, но записан не полностью "
                f"(шаг {published['failed_stage']}) — расчёт оценок по нему не запущен.",
                str(published.get("error") or published["failed_stage"]),
            )
        )
    if published.get("buildings_failed"):
        warnings.append(
            (
                f"Не записано зданий: {published['buildings_failed']} из {published.get('buildings_total')}.",
                "buildings_failed",
            )
        )
    if published.get("services_failed"):
        warnings.append(
            (
                f"Не записано сервисов: {published['services_failed']} — здания, в которых они стоят, сохранены.",
                "services_failed",
            )
        )
    if published.get("unknown_service_names"):
        warnings.append(
            (
                "Сервисы без типа в справочнике Urban API не записаны: "
                f"{', '.join(published['unknown_service_names'])}.",
                "unknown_service_types",
            )
        )
    return warnings


def _broker_failure_message(published: dict[str, Any]) -> str:
    sent = [SCORING_SCOPE.get(event, event) for event in published.get("notified_events") or []]
    scoring = f"расчёт оценок запущен только по {', '.join(sent)}" if sent else "расчёт оценок не запущен"
    return f"Данные сценария {published.get('scenario_id')} записаны, но сообщение в брокер не отправлено — {scoring}."
