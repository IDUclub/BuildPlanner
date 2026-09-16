from typing import Any

from pydantic import BaseModel, Field


class ProfileSelectionSchema(BaseModel):
    profile_id: int
    profile_name: str
    indicator_id: int
    reason: str
    buildable: bool
    missing_indicator_ids: list[int] = Field(default_factory=list)
    scoreboard: list[dict[str, Any]] = Field(default_factory=list)


class PipelineResultSchema(BaseModel):
    """Результат синхронного прогона.

    `buildings` может отсутствовать: если GenBuilder упал или зоны выбранного профиля
    не застраиваются, зоны сами по себе остаются полезным ответом (ADR-0001, D5).
    """

    scenario_id: int
    profile: ProfileSelectionSchema
    indicators_overview: dict[str, Any] | None = Field(
        default=None,
        description="Показатели проекта: `highlights` — краткая сводка, `sections` — таблица по разделам",
    )
    zones: dict[str, Any]
    roads: dict[str, Any] | None = None
    buildings: dict[str, Any] | None = None
    targets_by_zone: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mapping_summary: dict[str, Any] = Field(default_factory=dict)
    published: dict[str, Any] | None = Field(
        default=None,
        description="Куда сохранён результат: `scenario_id` сервисного сценария, по нему считаются оценки",
    )
    sirtep: dict[str, Any] | None = Field(
        default=None,
        description="Ответы SIRTEP как есть: `schedule` — очередь строительства, `provision` — ТЭПы",
    )
    summary: dict[str, Any] | None = Field(
        default=None,
        description="Справка по прогону: застройка, публикация, очередь строительства, обеспеченность",
    )
    warnings: list[str] = Field(default_factory=list)
