"""Клиент SIRTEP: профиль всегда 1, а незавершённый расчёт ТЭПов нужно переждать."""

from typing import Any

import pytest
from fastapi import HTTPException

from app.clients import sirtep_client as module
from app.clients.sirtep_client import SCHEDULER_PATH, TEPS_PATH, SirtepClient

READY = {"periods": [1, 2], "provision": [{"school": 0.4}, {"school": 0.9}], "unbuilt_services": []}
IN_PROGRESS = {"status": "in_progress", "progress": 0.3, "message": "считаю"}


class FakeHandler:
    """Отдаёт заготовленные ответы по очереди; исключение в списке — поднимается."""

    def __init__(self, answers: list[Any] | None = None):
        self.answers = answers or []
        self.calls: list[dict[str, Any]] = []

    async def get(self, path, params=None, headers=None):
        self.calls.append({"path": path, "params": params, "headers": headers})
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeTokens:
    def __init__(self):
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        return "service-token"


@pytest.fixture(name="no_sleep")
def no_sleep_fixture(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    return slept


def build(answers: list[Any] | None = None, **kwargs) -> tuple[SirtepClient, FakeHandler]:
    handler = FakeHandler(answers)
    return SirtepClient(handler, FakeTokens(), **kwargs), handler


@pytest.mark.asyncio
async def test_schedule_asks_for_the_provision_branch():
    """Профиль 1 — не наш выбор территории: только он даёт обеспеченность и принимается `/teps`."""
    client, handler = build([{"provision": {}, "simple": None}], periods=12, max_area_per_period=50_000)
    await client.schedule(scenario_id=777)
    assert handler.calls[-1]["path"] == SCHEDULER_PATH
    assert handler.calls[-1]["params"] == {
        "scenario_id": 777,
        "profile_id": 1,
        "periods": 12,
        "max_area_per_period": 50_000,
    }


@pytest.mark.asyncio
async def test_run_options_override_the_configured_pace():
    client, handler = build([{}], periods=40, max_area_per_period=100_000)
    await client.schedule(scenario_id=1, periods=8, max_area_per_period=25_000)
    assert handler.calls[-1]["params"]["periods"] == 8
    assert handler.calls[-1]["params"]["max_area_per_period"] == 25_000


@pytest.mark.asyncio
async def test_service_token_goes_with_every_request():
    """Сценарий лежит в проекте сервисной учётки: с пользовательским токеном SIRTEP его не увидит."""
    client, handler = build([{}])
    await client.schedule(scenario_id=1)
    assert handler.calls[-1]["headers"] == {"Authorization": "Bearer service-token"}


@pytest.mark.asyncio
async def test_server_error_on_teps_means_not_ready_yet(no_sleep):
    """0.3.1 на незавершённом расчёте отвечает 500 «кэш повреждён», а не «в процессе»."""
    broken = HTTPException(status_code=500, detail={"msg": "кэш повреждён"})
    client, handler = build([broken, broken, READY], poll_seconds=3)
    assert await client.provision(scenario_id=777, timeout_seconds=60) == READY
    assert [call["path"] for call in handler.calls] == [TEPS_PATH] * 3
    assert no_sleep == [3, 3]


@pytest.mark.asyncio
async def test_in_progress_payload_is_polled_too(no_sleep):
    client, _ = build([IN_PROGRESS, READY])
    assert await client.provision(scenario_id=777, timeout_seconds=60) == READY


@pytest.mark.asyncio
async def test_client_error_is_not_retried(no_sleep):
    """404 или 422 повтором не лечатся — это ошибка запроса, а не незавершённый расчёт."""
    client, handler = build([HTTPException(status_code=422, detail={"msg": "нет такого профиля"})])
    with pytest.raises(HTTPException) as failure:
        await client.provision(scenario_id=777, timeout_seconds=60)
    assert failure.value.status_code == 422
    assert len(handler.calls) == 1


@pytest.mark.asyncio
async def test_timeout_reports_the_last_answer(no_sleep):
    client, _ = build([HTTPException(status_code=500, detail={"msg": "кэш повреждён"})])
    with pytest.raises(HTTPException) as failure:
        await client.provision(scenario_id=777, timeout_seconds=0)
    assert failure.value.status_code == 504
    assert failure.value.detail["detail"] == {"msg": "кэш повреждён"}
