from typing import Any

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler


class GenBuilderClient:
    """Генерация застройки по готовым блокам.

    Используется путь `by_territory`: зоны GenPlanner приходят прямым GeoJSON'ом,
    без промежуточного сохранения в Urban API (ADR-0001, D3).

    Форма тела сверена с `TerritoryRequest` (ветка `main`):

    - `blocks` — FeatureCollection полигонов, у каждого обязателен непустой
      `properties.zone` и непустая геометрия Polygon/MultiPolygon;
    - `targets_by_zone` — **параметр снаружи, зона внутри**:
      `{"floors_avg": {"residential": 8}, ...}`. Обратная раскладка проходит
      валидацию (тип обеих — `dict[str, dict]`) и молча игнорируется;
    - `params` — гиперпараметры инференса, у сервиса есть свои дефолты;
    - `existing_buildings`, `generation_parameters` — необязательные;
    - `territory_id` — регион, чьи нормативы расставляют сервисы в жилых кварталах.
      Без него GenBuilder сервисы не генерирует.

    Ручка `by_territory` не требует авторизации (в отличие от `by_scenario`),
    но заголовок отправляется: он безвреден и нужен остальным ручкам сервиса.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler

    async def generate_by_territory(
        self,
        token: str,
        blocks: dict[str, Any],
        targets_by_zone: dict[str, dict[str, Any]],
        existing_buildings: dict[str, Any] | None = None,
        generation_parameters: dict[str, Any] | None = None,
        territory_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"blocks": blocks, "targets_by_zone": targets_by_zone}
        if existing_buildings:
            payload["existing_buildings"] = existing_buildings
        if generation_parameters:
            payload["generation_parameters"] = generation_parameters
        if territory_id is not None:
            payload["territory_id"] = territory_id
        return await self._api.post(
            "/generate/by_territory",
            json_data=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def get_properties_schema(self) -> dict[str, Any]:
        return await self._api.get("/generate/properties_schema")


def building_services(feature: dict[str, Any]) -> list[tuple[str, Any]]:
    """Services placed in a GenBuilder building as `(name, capacity)`.

    GenBuilder sends them as `properties.service = [{name: capacity}]`, an empty list for other buildings.
    """
    raw = (feature.get("properties") or {}).get("service")
    return [
        (str(name), capacity)
        for entry in (raw if isinstance(raw, list) else [])
        if isinstance(entry, dict)
        for name, capacity in entry.items()
    ]
