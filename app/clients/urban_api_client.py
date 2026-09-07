from typing import Any, Iterable

from loguru import logger

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler


class UrbanApiClient:
    """Доступ к показателям сценария.

    Схема сверена с OpenAPI стенда (`ScenarioIndicatorValue`):
    ответ — плоский массив, `indicator_id` лежит внутри вложенного `indicator`,
    полей `date_value`/`value_type` не существует — свежесть определяет `updated_at`.
    Значение может быть привязано к гексагону: у таких строк заполнен `hexagon_id`.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler

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

    async def get_scenario(self, scenario_id: int, token: str) -> dict[str, Any]:
        return await self._api.get(
            f"/api/v1/scenarios/{scenario_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    @staticmethod
    def latest_values_by_indicator(
        raw_values: Iterable[dict[str, Any]],
        indicator_ids: Iterable[int],
    ) -> dict[int, float]:
        """Оставляет по одному значению на индикатор.

        Приоритет — территориальное значение (`hexagon_id` пуст): оно относится ко всей
        территории, тогда как гексагональное описывает одну ячейку. Сравнивать агрегат
        одного индикатора с ячейкой другого нельзя. Среди равных по этому признаку
        побеждает самое свежее по `updated_at`.
        """
        wanted = set(indicator_ids)
        best: dict[int, tuple[tuple[int, str], float]] = {}

        for item in raw_values:
            indicator_id = _extract_indicator_id(item)
            if indicator_id is None or indicator_id not in wanted:
                continue
            value = _extract_value(item)
            if value is None:
                continue

            rank = (
                1 if item.get("hexagon_id") is None else 0,
                str(item.get("updated_at") or item.get("created_at") or ""),
            )
            previous = best.get(indicator_id)
            if previous is None or rank >= previous[0]:
                best[indicator_id] = (rank, value)

        missing = wanted - set(best)
        if missing:
            logger.warning("У сценария нет значений по индикаторам: {}", sorted(missing))
        return {indicator_id: value for indicator_id, (_, value) in best.items()}


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
