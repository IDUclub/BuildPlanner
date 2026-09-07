from fastapi import Request

from app.chat.chat_service import ChatService
from app.pipeline.pipeline_service import PipelineService
from app.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.config


def get_pipeline_service(request: Request) -> PipelineService:
    return request.app.state.pipeline_service


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service
