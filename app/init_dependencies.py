"""Все синглтоны собираются здесь один раз при старте и живут в `app.state`.

Та же схема, что в GenPlanner и GenBuilder: сборка — тут, чтение — в `dependencies.py`.
"""

from fastapi import FastAPI
from loguru import logger

from app.chat.chat_service import ChatService
from app.clients.genbuilder_client import GenBuilderClient
from app.clients.genplanner_client import GenPlannerClient
from app.clients.score_watcher import ScoreWatcher
from app.clients.sirtep_client import SirtepClient
from app.clients.urban_api_client import UrbanApiClient
from app.clients.urban_scenario_writer import UrbanScenarioWriter
from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.auth.service_token import ServiceTokenProvider
from app.common.chat_storage.chat_storage_client import ChatStorageClient
from app.common.llm.vllm_chat_client import VLLMChatClient
from app.common.logging.init_logger import init_logger
from app.common.object_storage.object_storage import ObjectStorage, ObjectStorageError, build_object_storage
from app.pipeline.geo_layers import LayerStore
from app.pipeline.pipeline_service import PipelineService
from app.pipeline.scenario_publisher import ScenarioPublisher
from app.settings import Settings


def _build_object_storage(settings: Settings) -> ObjectStorage | None:
    """Без хранилища сервис работает, но слои прогона не переживут перезагрузку чата."""
    try:
        storage = build_object_storage(
            address=settings.minio_address,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket_name,
            region=settings.minio_region,
            outputs_dir=settings.outputs_dir,
        )
    except (ObjectStorageError, ImportError) as exc:
        logger.error("Хранилище слоёв не собрано, в историю чата слои не попадут: {}", exc)
        return None
    if not settings.public_base_url:
        logger.warning("PUBLIC_BASE_URL не задан: ссылки на слои строятся от адреса входящего запроса")
    return storage


def init_dependencies(app: FastAPI) -> None:
    settings = Settings()
    app.state.config = settings
    app.state.log_path = init_logger(settings.log_level, settings.log_file)

    urban_client = UrbanApiClient(
        AsyncJsonApiHandler(settings.urban_api, settings.urban_api_timeout_seconds, "Urban API")
    )
    genplanner_client = GenPlannerClient(
        AsyncJsonApiHandler(settings.genplanner_api, settings.genplanner_timeout_seconds, "GenPlanner")
    )
    genbuilder_client = GenBuilderClient(
        AsyncJsonApiHandler(settings.genbuilder_api, settings.genbuilder_timeout_seconds, "GenBuilder")
    )

    token_provider = None
    if settings.service_account_enabled:
        token_provider = ServiceTokenProvider(
            settings.keycloak_url,
            settings.keycloak_realm,
            settings.keycloak_client_id,
            settings.keycloak_client_secret,
        )

    publisher = None
    if settings.urban_write_enabled and token_provider is not None:
        publisher = ScenarioPublisher(
            urban_client=urban_client,
            writer=UrbanScenarioWriter(
                AsyncJsonApiHandler(settings.urban_api, settings.urban_api_timeout_seconds, "Urban API"),
                token_provider,
                zone_source=settings.publish_zone_source,
                max_concurrency=settings.publish_max_concurrency,
            ),
            project_prefix=settings.service_project_prefix,
        )
    else:
        logger.warning("Запись в Urban API выключена: оценки по сгенерированному сценарию считаться не будут")

    sirtep_client = None
    if settings.sirtep_enabled and token_provider is not None:
        sirtep_client = SirtepClient(
            AsyncJsonApiHandler(settings.sirtep_api, settings.sirtep_timeout_seconds, "SIRTEP"),
            token_provider,
            periods=settings.sirtep_periods,
            max_area_per_period=settings.sirtep_max_area_per_period,
            provision_timeout_seconds=settings.sirtep_provision_timeout_seconds,
            poll_seconds=settings.sirtep_poll_seconds,
        )
    else:
        logger.warning("SIRTEP не настроен: очерёдность строительства считаться не будет")

    score_watcher = None
    if settings.score_wait_active and token_provider is not None:
        score_watcher = ScoreWatcher(
            urban_client,
            token_provider,
            expected_ids=settings.score_indicator_id_list,
            timeout_seconds=settings.score_wait_timeout_seconds,
            poll_seconds=settings.score_poll_seconds,
        )
    else:
        logger.warning("Ожидание оценок выключено: итоговая сводка выйдет без оценок сторонних сервисов")

    app.state.object_storage = _build_object_storage(settings)
    app.state.pipeline_service = PipelineService(
        urban_client=urban_client,
        genplanner_client=genplanner_client,
        genbuilder_client=genbuilder_client,
        cache_ttl_seconds=settings.genplanner_cache_ttl_seconds,
        publisher=publisher,
        layer_store=(
            LayerStore(
                app.state.object_storage,
                settings.public_base_url,
                settings.geo_layer_url_ttl_seconds,
            )
            if app.state.object_storage is not None
            else None
        ),
        sirtep_client=sirtep_client,
        score_watcher=score_watcher,
    )

    llm_client = None
    if settings.llm_enabled:
        llm_client = VLLMChatClient(
            base_url=settings.llm_api,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    else:
        logger.warning("LLM не настроена: чат будет запускать прогон без разбора реплики")

    chat_storage = None
    if settings.chat_storage_enabled and token_provider is not None:
        chat_storage = ChatStorageClient(
            AsyncJsonApiHandler(settings.chat_storage_api, 60, "ChatStorage"),
            token_provider,
        )
    else:
        logger.warning("ChatStorage не настроен: история диалога сохраняться не будет")

    app.state.chat_service = ChatService(app.state.pipeline_service, llm_client, chat_storage)
    logger.info("Зависимости собраны, окружение: {}", settings.app_env)
