"""Все синглтоны собираются здесь один раз при старте и живут в `app.state`.

Та же схема, что в GenPlanner и GenBuilder: сборка — тут, чтение — в `dependencies.py`.
"""

from fastapi import FastAPI
from loguru import logger

from app.chat.chat_service import ChatService
from app.clients.genbuilder_client import GenBuilderClient
from app.clients.genplanner_client import GenPlannerClient
from app.clients.urban_api_client import UrbanApiClient
from app.clients.urban_scenario_writer import UrbanScenarioWriter
from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.auth.service_token import ServiceTokenProvider
from app.common.chat_storage.chat_storage_client import ChatStorageClient
from app.common.llm.vllm_chat_client import VLLMChatClient
from app.common.logging.init_logger import init_logger
from app.pipeline.pipeline_service import PipelineService
from app.pipeline.scenario_publisher import ScenarioPublisher
from app.settings import Settings


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

    app.state.pipeline_service = PipelineService(
        urban_client=urban_client,
        genplanner_client=genplanner_client,
        genbuilder_client=genbuilder_client,
        cache_ttl_seconds=settings.genplanner_cache_ttl_seconds,
        publisher=publisher,
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
