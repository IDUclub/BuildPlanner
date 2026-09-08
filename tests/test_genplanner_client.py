"""Форма запроса к GenPlanner. Сверена с `GenPlannerFuncZonesDTO` ветки master.

Ручка объявлена как `Annotated[DTO, Depends(DTO)]`, поэтому FastAPI кладёт скаляры
в query, а составные поля — в тело. Перепутать местами значит получить 422.
"""

from typing import Any

import pytest

from app.clients.genplanner_client import GenPlannerClient

QUERY_FIELDS = {
    "project_id",
    "scenario_id",
    "roads_extend_distance",
    "elevation_angle",
    "ignore_default_relations",
    "test",
}
BODY_FIELDS = {
    "fix_zones",
    "min_block_area",
    "functional_zones",
    "territory_balance",
    "neighbour_pairs",
    "forbidden_pairs",
}


class FakeHandler:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def get(self, path, params=None, headers=None):
        self.calls.append({"method": "GET", "path": path, "params": params, "headers": headers})
        return {}

    async def post(self, path, json_data=None, params=None, headers=None):
        self.calls.append({"method": "POST", "path": path, "json": json_data, "params": params, "headers": headers})
        return {"zones": {}, "roads": {}}


@pytest.fixture(name="handler")
def handler_fixture() -> FakeHandler:
    return FakeHandler()


async def _run(handler: FakeHandler, **overrides) -> dict[str, Any]:
    kwargs = {
        "token": "t",
        "scenario_id": 835,
        "project_id": 120,
        "territory_balance": {"13": 0.7, "2": 0.3},
        **overrides,
    }
    await GenPlannerClient(handler).run_func_generation(**kwargs)
    return handler.calls[-1]


@pytest.mark.asyncio
async def test_scalars_go_to_query(handler):
    call = await _run(handler)
    assert call["params"]["project_id"] == 120
    assert call["params"]["scenario_id"] == 835
    assert set(call["params"]) <= QUERY_FIELDS


@pytest.mark.asyncio
async def test_territory_balance_goes_to_body(handler):
    call = await _run(handler)
    assert call["json"] == {"territory_balance": {"13": 0.7, "2": 0.3}}
    assert set(call["json"]) <= BODY_FIELDS


@pytest.mark.asyncio
async def test_test_flag_is_serialized_for_the_query_string(handler):
    """aiohttp не умеет класть bool в params — нужна строка."""
    call = await _run(handler, test=True)
    assert call["params"]["test"] == "true"


@pytest.mark.asyncio
async def test_optional_elevation_angle_is_omitted_when_unset(handler):
    call = await _run(handler)
    assert "elevation_angle" not in call["params"]


@pytest.mark.asyncio
async def test_zones_reference_path_is_double_prefixed(handler):
    """Роутер смонтирован на `/genplanner`, а сам путь внутри него — `/gen_planner/...`."""
    await GenPlannerClient(handler).get_zones_reference()
    assert handler.calls[-1]["path"] == "/genplanner/gen_planner/zones_reference"
