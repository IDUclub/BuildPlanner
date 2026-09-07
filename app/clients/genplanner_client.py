from typing import Any

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler

GENPLANNER_PREFIX = "/genplanner"


class GenPlannerClient:
    """Генерация территориальных зон и дорог.

    Профиль попадает в генерацию через баланс территориальных зон:
    `GET /default/func_ratio?zone={profile_id}` отдаёт дефолтное соотношение зон
    для профиля, оно же уходит в `run_func_generation` как `territory_balance`.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler

    async def get_default_func_ratio(self, profile_id: int) -> dict[str, float]:
        return await self._api.get(f"{GENPLANNER_PREFIX}/default/func_ratio", params={"zone": profile_id})

    async def get_zones_reference(self) -> list[dict[str, Any]]:
        return await self._api.get(f"{GENPLANNER_PREFIX}/zones_reference")

    async def run_func_generation(
        self,
        token: str,
        scenario_id: int,
        territory_balance: dict[str, float],
        project_id: int | None = None,
        test: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Возвращает `{zones: FeatureCollection, roads: FeatureCollection}`."""
        payload: dict[str, Any] = {
            "scenario_id": scenario_id,
            "territory_balance": territory_balance,
            "test": test,
        }
        if project_id is not None:
            payload["project_id"] = project_id
        if extra:
            payload.update(extra)
        return await self._api.post(
            f"{GENPLANNER_PREFIX}/run_func_generation",
            json_data=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def run_custom_func_generation(
        self,
        token: str,
        profile_id: int,
        territory: dict[str, Any],
    ) -> dict[str, Any]:
        """Путь без сценария: профиль + произвольный полигон территории."""
        return await self._api.post(
            f"{GENPLANNER_PREFIX}/custom/run_func_generation",
            json_data={"profile_id": profile_id, "territory": territory},
            headers={"Authorization": f"Bearer {token}"},
        )
