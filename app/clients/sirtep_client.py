"""Очерёдность строительства по опубликованному сценарию.

SIRTEP — оптимизатор очереди застройки, а не проверка мастер-плана: он раскладывает
уже записанные дома и сервисы по периодам и считает, как по ходу стройки растёт
обеспеченность.

Токен он не проверяет, а передаёт в Urban API как есть, поэтому ходим сервисным:
сгенерированный сценарий лежит в проекте сервисной учётки, и пользовательскому токену
он не виден. Отсюда же `ServiceTokenProvider` в конструкторе — как у writer'а.

Профиль всегда `PROVISION_PROFILE_ID`: в provision-ветке профиль выбирает только саму
ветку, а `/optimize/teps` версии 0.3.1 принимает лишь 1, 2 и 8.
"""

import asyncio
import time
from typing import Any

from fastapi import HTTPException
from loguru import logger

from app.common.api_handlers.json_api_handler import AsyncJsonApiHandler
from app.common.auth.service_token import ServiceTokenProvider
from app.common.exceptions.http_exception import http_exception

PROVISION_PROFILE_ID = 1

SCHEDULER_PATH = "/optimize/scheduler"
TEPS_PATH = "/optimize/teps"


class SirtepClient:
    """Настройки темпа держит клиент, прогон их переопределяет по месту."""

    def __init__(
        self,
        handler: AsyncJsonApiHandler,
        token_provider: ServiceTokenProvider,
        periods: int = 40,
        max_area_per_period: int = 100_000,
        provision_timeout_seconds: int = 300,
        poll_seconds: int = 5,
    ):
        self._api = handler
        self._tokens = token_provider
        self._periods = periods
        self._max_area_per_period = max_area_per_period
        self._provision_timeout = provision_timeout_seconds
        self._poll_seconds = max(poll_seconds, 1)

    async def schedule(
        self,
        *,
        scenario_id: int,
        periods: int | None = None,
        max_area_per_period: int | None = None,
    ) -> dict[str, Any]:
        """Очередь строительства: ветка `provision` для профилей 1, 2, 8, 10–13, `simple` для прочих."""
        answer = await self._api.get(
            SCHEDULER_PATH,
            params=self._params(scenario_id, periods, max_area_per_period),
            headers=await self._headers(),
        )
        return answer if isinstance(answer, dict) else {}

    async def provision(
        self,
        *,
        scenario_id: int,
        periods: int | None = None,
        max_area_per_period: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """ТЭПы: обеспеченность по периодам. Считаются фоном после `/scheduler`, поэтому опрос.

        5xx до истечения таймаута — это «ещё не готово», а не отказ: версия 0.3.1 на
        незавершённом расчёте отвечает 500 «кэш повреждён», потому что кладёт задачу
        в кэш с датой в идентификаторе, а ищет без неё.
        """
        timeout = self._provision_timeout if timeout_seconds is None else timeout_seconds
        params = self._params(scenario_id, periods, max_area_per_period)
        deadline = time.monotonic() + timeout
        last_detail: Any = None

        while True:
            try:
                answer = await self._api.get(TEPS_PATH, params=params, headers=await self._headers())
            except HTTPException as exc:
                if exc.status_code < 500:
                    raise
                last_detail = exc.detail
            else:
                if isinstance(answer, dict) and "provision" in answer:
                    return answer
                last_detail = answer

            if time.monotonic() >= deadline:
                raise http_exception(
                    504,
                    "SIRTEP не посчитал ТЭПы за отведённое время",
                    _input={"scenario_id": scenario_id, "timeout": timeout},
                    _detail=last_detail,
                )
            logger.debug("ТЭПы сценария {} ещё считаются, повтор через {} с", scenario_id, self._poll_seconds)
            await asyncio.sleep(self._poll_seconds)

    def _params(self, scenario_id: int, periods: int | None, max_area_per_period: int | None) -> dict[str, Any]:
        return {
            "scenario_id": scenario_id,
            "profile_id": PROVISION_PROFILE_ID,
            "periods": self._periods if periods is None else periods,
            "max_area_per_period": (self._max_area_per_period if max_area_per_period is None else max_area_per_period),
        }

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.get_token()}"}
