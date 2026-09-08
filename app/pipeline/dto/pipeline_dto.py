from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.common.constants.pipeline_constants import PROFILE_NAMES


class PipelineOptionsDTO(BaseModel):
    """Необязательные ручные переопределения. Пустой объект — полностью автоматический прогон."""

    profile_id: int | None = Field(
        default=None,
        description="Переопределить профиль вместо выбора по индикаторам",
    )
    project_id: int | None = Field(
        default=None,
        description="Проект сценария; по умолчанию берётся из самого сценария в Urban API",
    )
    territory_balance: dict[str, float] | None = Field(
        default=None,
        description="Переопределить баланс территориальных зон для GenPlanner",
    )
    targets_overrides: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Точечные правки targets_by_zone: {зона GenBuilder: {параметр: значение}}",
    )
    skip_generation: bool = Field(
        default=False,
        description="Остановиться после зон GenPlanner, не вызывая GenBuilder",
    )
    publish: bool = Field(
        default=True,
        description="Сохранить результат в Urban API под сервисной учёткой и запустить расчёт оценок",
    )
    test: bool = Field(default=False, description="Использовать тестовый контур Urban API в GenPlanner")

    @field_validator("profile_id")
    @classmethod
    def _known_profile(cls, value: int | None) -> int | None:
        if value is not None and value not in PROFILE_NAMES:
            raise ValueError(f"Неизвестный profile_id: {value}. Доступные: {sorted(PROFILE_NAMES)}")
        return value
