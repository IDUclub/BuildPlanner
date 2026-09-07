from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.chat import chat_controller
from app.init_dependencies import init_dependencies
from app.pipeline import pipeline_controller
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
)

app.include_router(pipeline_controller.router)
app.include_router(chat_controller.router)
app.include_router(logs_router.router)
