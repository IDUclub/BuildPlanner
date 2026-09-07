from pydantic import BaseModel, Field

from app.pipeline.dto.pipeline_dto import PipelineOptionsDTO


class ChatTurnDTO(BaseModel):
    """Один ход диалога. Тот же контракт, что у `ChatTurnDTO` в GenPlanner."""

    user_query: str = Field(description="Реплика пользователя")
    chat_id: str | None = Field(default=None, description="Существующий чат; пусто — начнётся новый")
    options: PipelineOptionsDTO | None = Field(
        default=None,
        description="Переопределения из UI; реплика пользователя может их дополнить",
    )
