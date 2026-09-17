"""Форма запроса к GenBuilder `by_territory`: регион уходит только когда он известен."""

from typing import Any

import pytest

from app.clients.genbuilder_client import GenBuilderClient

BLOCKS = {"type": "FeatureCollection", "features": []}
TARGETS = {"residents": {"residential": 3000}}


class FakeHandler:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def post(self, path, json_data=None, headers=None):
        self.calls.append({"path": path, "json": json_data, "headers": headers})
        return {"type": "FeatureCollection", "features": []}


@pytest.fixture(name="handler")
def handler_fixture() -> FakeHandler:
    return FakeHandler()


@pytest.mark.asyncio
async def test_region_is_sent_for_services(handler):
    """Без `territory_id` GenBuilder не знает нормативов и не ставит ни школ, ни садов."""
    await GenBuilderClient(handler).generate_by_territory("t", BLOCKS, TARGETS, territory_id=1)
    assert handler.calls[-1]["json"]["territory_id"] == 1


@pytest.mark.asyncio
async def test_unknown_region_is_not_sent(handler):
    await GenBuilderClient(handler).generate_by_territory("t", BLOCKS, TARGETS)
    assert handler.calls[-1]["json"] == {"blocks": BLOCKS, "targets_by_zone": TARGETS}
