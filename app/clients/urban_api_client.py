import time
from typing import Any, Iterable

from loguru import logger

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.exceptions.http_exception import http_exception

INDICATOR_GROUPS_TTL_SECONDS = 3600


class UrbanApiClient:
    """Доступ к показателям сценария.

    Схема сверена с OpenAPI стенда (`ScenarioIndicatorValue`):
    ответ — плоский массив, `indicator_id` лежит внутри вложенного `indicator`,
    полей `date_value`/`value_type` не существует — свежесть определяет `updated_at`.
    Значение может быть привязано к гексагону: у таких строк заполнен `hexagon_id`.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler
        self._groups_cache: tuple[float, list[dict[str, Any]]] | None = None

    async def get_scenario_indicators(
        self,
        scenario_id: int,
        token: str,
        indicator_ids: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Фильтрация по индикаторам делается на стороне Urban API.

        У сценария сотни значений (в том числе по каждому гексагону), а нужны десять —
        `indicator_ids` принимает список id через запятую.
        """
        params: dict[str, Any] = {}
        if indicator_ids:
            params["indicator_ids"] = ",".join(str(indicator_id) for indicator_id in indicator_ids)
        return await self._api.get(
            f"/api/v1/scenarios/{scenario_id}/indicators_values",
            params=params or None,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def get_indicator_groups(self, token: str) -> list[dict[str, Any]]:
        """Справочник групп показателей — из него берутся разделы таблицы.

        Данные справочные и меняются редко, поэтому держим их в памяти: иначе каждый
        прогон тянул бы почти две сотни описаний ради заголовков разделов.
        """
        cached = self._groups_cache
        if cached and time.monotonic() - cached[0] < INDICATOR_GROUPS_TTL_SECONDS:
            return cached[1]

        groups = await self._api.get(
            "/api/v1/indicators_groups",
            headers={"Authorization": f"Bearer {token}"},
        )
        groups = groups if isinstance(groups, list) else []
        self._groups_cache = (time.monotonic(), groups)
        return groups

    async def get_scenario(self, scenario_id: int, token: str) -> dict[str, Any]:
        return await self._api.get(
            f"/api/v1/scenarios/{scenario_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def get_project_ref(self, scenario_id: int, token: str) -> tuple[int, int | None]:
        """`project_id` и регион проекта — из одного ответа, чтобы не ходить дважды.

        `project_id` GenPlanner требует отдельным параметром. Регион (`project.region.id`)
        нужен GenBuilder — по его нормативам расставляются сервисы — и публикации:
        `ProjectPost.territory_id` подписан как «project region identifier».
        """
        scenario = await self.get_scenario(scenario_id, token)
        project = (scenario or {}).get("project") or {}
        project_id = project.get("project_id")
        if not isinstance(project_id, int) or isinstance(project_id, bool):
            raise http_exception(
                404,
                "У сценария не удалось определить проект",
                _input={"scenario_id": scenario_id},
                _detail={"project": project},
            )
        region_id = (project.get("region") or {}).get("id")
        return project_id, region_id if isinstance(region_id, int) else None

    async def get_project_geometry(self, project_id: int, token: str) -> dict[str, Any] | None:
        """Граница проекта — её же получает проект-контейнер сервисной учётки."""
        territory = await self._api.get(
            f"/api/v1/projects/{project_id}/territory",
            headers={"Authorization": f"Bearer {token}"},
        )
        geometry = (territory or {}).get("geometry")
        return geometry if isinstance(geometry, dict) else None

    @staticmethod
    def latest_rows_by_indicator(
        raw_values: Iterable[dict[str, Any]],
        indicator_ids: Iterable[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Оставляет по одной строке на индикатор.

        Приоритет — территориальное значение (`hexagon_id` пуст): оно относится ко всей
        территории, тогда как гексагональное описывает одну ячейку. Сравнивать агрегат
        одного индикатора с ячейкой другого нельзя. Среди равных по этому признаку
        побеждает самое свежее по `updated_at`.

        `indicator_ids = None` — взять все, что есть у сценария.
        """
        wanted = None if indicator_ids is None else set(indicator_ids)
        best: dict[int, tuple[tuple[int, str], dict[str, Any]]] = {}

        for item in raw_values:
            indicator_id = _extract_indicator_id(item)
            if indicator_id is None or (wanted is not None and indicator_id not in wanted):
                continue
            if _extract_value(item) is None:
                continue

            rank = (
                1 if item.get("hexagon_id") is None else 0,
                str(item.get("updated_at") or item.get("created_at") or ""),
            )
            previous = best.get(indicator_id)
            if previous is None or rank >= previous[0]:
                best[indicator_id] = (rank, item)

        return {indicator_id: item for indicator_id, (_, item) in best.items()}

    @classmethod
    def latest_values_by_indicator(
        cls,
        raw_values: Iterable[dict[str, Any]],
        indicator_ids: Iterable[int],
    ) -> dict[int, float]:
        """То же, но одними числами — форма, в которой значения нужны выбору профиля."""
        wanted = set(indicator_ids)
        rows = cls.latest_rows_by_indicator(raw_values, wanted)

        missing = wanted - set(rows)
        if missing:
            logger.warning("У сценария нет значений по индикаторам: {}", sorted(missing))
        # Значение уже проверено при отборе строк, поэтому None здесь не встречается.
        return {indicator_id: float(_extract_value(row) or 0.0) for indicator_id, row in rows.items()}


def _extract_indicator_id(item: dict[str, Any]) -> int | None:
    """`ScenarioIndicatorValue.indicator.indicator_id`; плоский вариант — на случай иных ручек."""
    nested = item.get("indicator")
    if isinstance(nested, dict) and isinstance(nested.get("indicator_id"), int):
        return nested["indicator_id"]
    raw = item.get("indicator_id")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _extract_value(item: dict[str, Any]) -> float | None:
    raw = item.get("value")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            return None
    return None
