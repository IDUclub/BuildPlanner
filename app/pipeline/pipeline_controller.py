from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.common.auth.bearer import verify_bearer_token
from app.common.constants.pipeline_constants import (
    GENPLANNER_TO_GENBUILDER_ZONE,
    INDICATOR_NAMES,
    INDICATOR_TO_PROFILE,
    PROFILE_NAMES,
    PROFILE_TARGETS,
    SELECTION_INDICATOR_IDS,
)
from app.common.exceptions.http_exception import http_exception
from app.common.object_storage.object_storage import ObjectStorage, ObjectStorageError
from app.dependencies import get_object_storage, get_pipeline_service, get_settings
from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO
from app.pipeline.geo_layers import MIME_TYPE, object_key
from app.pipeline.pipeline_service import PipelineService
from app.pipeline.schema.pipeline_schema import PipelineResultSchema
from app.utils.sse import sse_stream

router = APIRouter(prefix="/buildplanner", tags=["pipeline"])


@router.post(
    "/scenarios/{scenario_id}/run",
    response_model=PipelineResultSchema,
    summary="Синхронный прогон пайплайна по сценарию",
)
async def run_pipeline(
    scenario_id: int,
    options: PipelineOptionsDTO | None = None,
    token: str = Depends(verify_bearer_token),
    service: PipelineService = Depends(get_pipeline_service),
) -> PipelineResultSchema:
    """Показатели -> профиль -> зоны GenPlanner -> застройка GenBuilder, одним ответом."""
    return await service.run(scenario_id, token, options or PipelineOptionsDTO())


@router.post(
    "/scenarios/{scenario_id}/run/stream",
    summary="Тот же прогон, но потоком событий (без чата)",
)
async def run_pipeline_stream(
    request: Request,
    scenario_id: int,
    options: PipelineOptionsDTO | None = None,
    token: str = Depends(verify_bearer_token),
    service: PipelineService = Depends(get_pipeline_service),
) -> EventSourceResponse:
    settings = get_settings(request)
    events_iterator = service.stream(
        scenario_id, token, options or PipelineOptionsDTO(), base_url=str(request.base_url)
    )
    return EventSourceResponse(
        sse_stream(events_iterator, tail=lambda: ServerSentEvent(event="done", data="{}")),
        ping=settings.sse_keepalive_seconds,
    )


@router.get(
    "/files/{slot}/{result_id}",
    summary="Слой прогона из хранилища — по ссылке из события `file` или из истории чата",
    response_model=None,
)
def layer_file(
    slot: str,
    result_id: str,
    storage: ObjectStorage | None = Depends(get_object_storage),
    settings = Depends(get_settings),
) -> StreamingResponse | RedirectResponse:
    """Долговечная ссылка: MinIO отдаётся по свежему presigned URL, локальный слой — потоком."""
    not_found = http_exception(404, "Слой не найден", _input={"slot": slot, "result_id": result_id})
    if storage is None:
        raise not_found
    try:
        key = object_key(result_id, slot)
        if not storage.exists(key):
            raise not_found
        if url := storage.presigned_url(key, settings.geo_layer_url_ttl_seconds):
            return RedirectResponse(url, status_code=307)
    except ValueError as exc:
        raise not_found from exc
    except ObjectStorageError as exc:
        raise http_exception(502, "Хранилище слоёв недоступно", _input={"slot": slot}, _detail=str(exc)) from exc
    return StreamingResponse(
        storage.open_stream(key),
        media_type=MIME_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{slot}.geojson"'},
    )


@router.get(
    "/scenarios/{scenario_id}/indicators",
    summary="Показатели проекта таблицей, без генерации",
)
async def scenario_indicators(
    scenario_id: int,
    token: str = Depends(verify_bearer_token),
    service: PipelineService = Depends(get_pipeline_service),
) -> dict[str, object]:
    """`highlights` — краткая сводка по территории, `sections` — всё остальное по разделам."""
    overview, error = await service.territory_indicators(scenario_id, token)
    if overview is None:
        raise http_exception(
            502,
            "Не удалось получить показатели сценария",
            _input={"scenario_id": scenario_id},
            _detail={"stage": "fetch_indicators", "error": error},
        )
    return overview


@router.get("/reference/indicators", summary="Индикаторы, участвующие в выборе профиля")
async def indicators_reference() -> list[dict[str, object]]:
    return [
        {
            "indicator_id": indicator_id,
            "name": INDICATOR_NAMES[indicator_id],
            "profile_id": INDICATOR_TO_PROFILE[indicator_id],
            "profile_name": PROFILE_NAMES[INDICATOR_TO_PROFILE[indicator_id]],
        }
        for indicator_id in SELECTION_INDICATOR_IDS
    ]


@router.get("/reference/profiles", summary="Профили GenPlanner и их застройка в GenBuilder")
async def profiles_reference() -> list[dict[str, object]]:
    reference = []
    for profile_id, profile_name in sorted(PROFILE_NAMES.items()):
        mapping = GENPLANNER_TO_GENBUILDER_ZONE.get(profile_id)
        reference.append(
            {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "genbuilder_zone": mapping[0] if mapping else None,
                "floors_group": mapping[1] if mapping else None,
                "buildable": mapping is not None,
                "targets": PROFILE_TARGETS.get(profile_id),
            }
        )
    return reference
