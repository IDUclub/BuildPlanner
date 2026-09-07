from typing import Any, Iterable

from loguru import logger

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler


class UrbanApiClient:
    """Доступ к показателям сценария.

    ВНИМАНИЕ: точная схема ответа `indicators_values` — единственный внешний контракт,
    который не удалось проверить по исходникам (OpenAPI стенда слишком большой).
    Разбор намеренно терпимый: имя поля значения и место `indicator_id` ищутся по вариантам.
    См. ADR-0001, пункт 1 плана работ.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler

    async def get_scenario_indicators(self, scenario_id: int, token: str) -> list[dict[str, Any]]:
        response = await self._api.get(
            f"/api/v1/scenarios/{scenario_id}/indicators_values",
            headers={"Authorization": f"Bearer {token}"},
        )
        if isinstance(response, dict):
            # некоторые ручки Urban API отдают постраничный результат
            response = response.get("results", response.get("items", []))
        return list(response or [])

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
        """Оставляет по одному — самому свежему — значению на индикатор.

        Свежесть определяется по `date_value`; при отсутствии даты выигрывает
        последняя запись в ответе (порядок Urban API).
        """
        wanted = set(indicator_ids)
        best: dict[int, tuple[str, float]] = {}

        for item in raw_values:
            indicator_id = _extract_indicator_id(item)
            if indicator_id is None or indicator_id not in wanted:
                continue
            value = _extract_value(item)
            if value is None:
                continue
            stamp = str(item.get("date_value") or item.get("created_at") or "")
            previous = best.get(indicator_id)
            if previous is None or stamp >= previous[0]:
                best[indicator_id] = (stamp, value)

        missing = wanted - set(best)
        if missing:
            logger.warning("У сценария нет значений по индикаторам: {}", sorted(missing))
        return {indicator_id: value for indicator_id, (_, value) in best.items()}


def _extract_indicator_id(item: dict[str, Any]) -> int | None:
    for key in ("indicator_id", "indicatorId"):
        if isinstance(item.get(key), int):
            return item[key]
    nested = item.get("indicator")
    if isinstance(nested, dict) and isinstance(nested.get("indicator_id"), int):
        return nested["indicator_id"]
    return None


def _extract_value(item: dict[str, Any]) -> float | None:
    for key in ("value", "indicator_value", "val"):
        raw = item.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw.replace(",", "."))
            except ValueError:
                continue
    return None
