from typing import Any

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler

GENPLANNER_PREFIX = "/genplanner"


class GenPlannerClient:
    """Генерация территориальных зон и дорог.

    Профиль попадает в генерацию через баланс территориальных зон:
    `GET /default/func_ratio?zone={profile_id}` отдаёт дефолтное соотношение зон
    для профиля, оно же уходит в `run_func_generation` как `territory_balance`.

    Форма запроса сверена с `GenPlannerFuncZonesDTO` (ветка master). Ручка объявлена
    как `Annotated[DTO, Depends(DTO)]`, а не как тело запроса, поэтому FastAPI
    раскладывает поля по двум местам: скаляры уходят в query, составные — в тело.
    """

    def __init__(self, handler: AsyncJsonApiHandler):
        self._api = handler

    async def get_default_func_ratio(self, profile_id: int) -> dict[str, float]:
        return await self._api.get(f"{GENPLANNER_PREFIX}/default/func_ratio", params={"zone": profile_id})

    async def get_zones_reference(self) -> list[dict[str, Any]]:
        """Путь двойной: роутер смонтирован с префиксом `/genplanner`, а сам путь — `/gen_planner/...`."""
        return await self._api.get(f"{GENPLANNER_PREFIX}/gen_planner/zones_reference")

    async def run_func_generation(
        self,
        token: str,
        scenario_id: int,
        project_id: int,
        territory_balance: dict[str, float],
        test: bool = False,
        elevation_angle: int | None = None,
        body_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Возвращает `{zones: FeatureCollection, roads: FeatureCollection}`.

        `project_id` в DTO обязателен: восстановление из сценария внутри GenPlanner
        до кода не доходит — запрос без него отсекает валидация FastAPI.
        """
        params: dict[str, Any] = {
            "project_id": project_id,
            "scenario_id": scenario_id,
            "test": str(test).lower(),
        }
        if elevation_angle is not None:
            params["elevation_angle"] = elevation_angle

        body: dict[str, Any] = {"territory_balance": territory_balance}
        if body_extra:
            body.update(body_extra)

        return await self._api.post(
            f"{GENPLANNER_PREFIX}/run_func_generation",
            json_data=body,
            params=params,
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
