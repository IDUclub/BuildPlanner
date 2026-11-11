from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.chat import chat_controller
from app.common.auth.bearer import verify_bearer_token
from app.dependencies import get_settings
from app.init_dependencies import init_dependencies
from app.pipeline import pipeline_controller
from app.resumable_streams import install_resumable_streams, keycloak_owner
from app.system import logs_router
from app.version import __version__


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_dependencies(application)
    yield


app = FastAPI(
    title="BuildPlanner API",
    version=__version__,
    description=(
        "Оркестратор пайплайна GenPlanner → GenBuilder. По id сценария выбирает профиль "
        "застройки из показателей Urban API, генерирует территориальные зоны и застраивает их."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(pipeline_controller.router)
app.include_router(chat_controller.router)
app.include_router(logs_router.router)


async def _stream_owner(request):
    token = await verify_bearer_token(request)
    settings = get_settings(request)
    return await keycloak_owner(token, settings.keycloak_url, settings.keycloak_realm)


install_resumable_streams(app, service="buildplanner", prefix="/buildplanner", authenticate=_stream_owner)
