from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.chat.chat_service import ChatService
from app.chat.dto.chat_dto import ChatTurnDTO
from app.common.auth.bearer import verify_bearer_token
from app.dependencies import get_chat_service, get_settings
from app.utils.sse import sse_stream

router = APIRouter(prefix="/buildplanner", tags=["chat"])


@router.post(
    "/scenarios/{scenario_id}/chat/stream",
    summary="Диалоговый прогон пайплайна через SSE",
    response_class=EventSourceResponse,
)
async def chat_stream(
    request: Request,
    scenario_id: int,
    turn: ChatTurnDTO,
    token: str = Depends(verify_bearer_token),
    service: ChatService = Depends(get_chat_service),
) -> EventSourceResponse:
    """События: `chat_created`, `token`, `progress`, `indicators`, `profile_selected`,
    `zones`, `roads`, `result`, `file`, `warning`, `error` и всегда последним `done`.

    HTTP-статус всегда 200 — фатальная ошибка приходит событием `error` внутри потока.
    """
    settings = get_settings(request)
    return EventSourceResponse(
        sse_stream(service.stream(scenario_id, turn, token, base_url=str(request.base_url))),
        ping=settings.sse_keepalive_seconds,
        headers={"X-Accel-Buffering": "no"},
    )
