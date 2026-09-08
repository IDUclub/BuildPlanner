"""Оркестрация пайплайна: показатели -> профиль -> зоны -> застройка.

Единственная реализация — асинхронный генератор событий `stream()`. Синхронный `run()`
просто вычерпывает его до конца, поэтому REST и чат гарантированно ходят одним путём
и не расходятся в поведении.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import HTTPException
from loguru import logger

from app.clients.genbuilder_client import GenBuilderClient
from app.clients.genplanner_client import GenPlannerClient
from app.clients.urban_api_client import UrbanApiClient
from app.common.constants.pipeline_constants import PROFILE_NAMES, SELECTION_INDICATOR_IDS
from app.common.exceptions.http_exception import http_exception
from app.pipeline import events
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.profile_selector import NoIndicatorValuesError, ProfileSelection, select_profile
from app.pipeline.schema.pipeline_schema import PipelineResultSchema, ProfileSelectionSchema
from app.pipeline.targets_policy import build_targets_by_zone, to_genbuilder_targets, zones_without_volume_target
from app.pipeline.zone_mapper import map_zones_to_blocks


@dataclass
class PipelineRun:
    """Состояние одного прогона: наполняется по ходу потока событий."""

    scenario_id: int
    selection: ProfileSelection | None = None
    zones: dict[str, Any] | None = None
    roads: dict[str, Any] | None = None
    buildings: dict[str, Any] | None = None
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
    ):
        self._urban = urban_client
        self._genplanner = genplanner_client
        self._genbuilder = genbuilder_client
        self._cache_ttl = cache_ttl_seconds
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
            zones=run.zones,
            roads=run.roads,
            buildings=run.buildings,
            targets_by_zone=run.targets_by_zone,
            mapping_summary=run.mapping_summary,
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
        except HTTPException as exc:
            run.failure = exc
            detail = exc.detail if isinstance(exc.detail, dict) else {"msg": str(exc.detail)}
            logger.error("Пайплайн сценария {} упал: {}", scenario_id, detail)
            yield events.error(stage=detail.get("stage", "pipeline"), detail=json.dumps(detail, ensure_ascii=False))

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

        # --- застройка
        run.targets_by_zone = build_targets_by_zone(
            selection.profile_id, mapping.zones_present, options.targets_overrides
        )
        idle_zones = zones_without_volume_target(run.targets_by_zone)
        if idle_zones:
            message = (
                f"У зон {', '.join(idle_zones)} нет цели объёма (residents/coverage_area) — "
                "GenBuilder их не застроит."
            )
            run.warnings.append(message)
            yield events.warning(events.STAGE_GENBUILDER, "no_volume_target", message)

        yield events.progress(events.STAGE_GENBUILDER)
        try:
            built = await self._genbuilder.generate_by_territory(
                token=token,
                blocks=mapping.blocks,
                targets_by_zone=to_genbuilder_targets(run.targets_by_zone),
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

    # ------------------------------------------------------------------ внутренности

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

        project_id = options.project_id or await self._urban.get_project_id(run.scenario_id, token)
        generated = await self._genplanner.run_func_generation(
            token=token,
            scenario_id=run.scenario_id,
            project_id=project_id,
            territory_balance=balance,
            test=options.test,
        )
        self._zones_cache[cache_key] = (time.monotonic(), generated)
        return generated

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
        summary = dict(built.get("summary") or {})
        summary.setdefault("buildings", len((run.buildings or {}).get("features", [])))
        summary["profile"] = run.selection.profile_name if run.selection else None
        summary["blocks"] = run.mapping_summary
        return summary
