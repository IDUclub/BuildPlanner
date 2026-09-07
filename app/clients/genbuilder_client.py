from typing import Any

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler


class GenBuilderClient:
    """Генерация застройки по готовым блокам.

    Используется путь `by_territory`: зоны GenPlanner приходят прямым GeoJSON'ом,
    без промежуточного сохранения в Urban API (ADR-0001, D3).
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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"blocks": blocks, "targets_by_zone": targets_by_zone}
        if existing_buildings:
            payload["existing_buildings"] = existing_buildings
        if generation_parameters:
            payload["generation_parameters"] = generation_parameters
        return await self._api.post(
            "/generate/by_territory",
            json_data=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def get_properties_schema(self) -> dict[str, Any]:
        return await self._api.get("/generate/properties_schema")
