"""Ожидание оценок по опубликованному сценарию.

«Слушать брокер» напрямую нельзя: доступа к нему у BuildPlanner нет — только
HTTP-фасад Urban API для отправки. Но сервисы, посчитав оценки, кладут их обратно
значениями индикаторов сценария, а их видно через ту же ручку `indicators_values`,
которую пайплайн уже читает. Поэтому ждём не сообщение, а появление свежих значений:
опрос с таймаутом, как у ТЭПов SIRTEP (`SirtepClient.provision`).

Ходим сервисным токеном: сгенерированный сценарий лежит в проекте сервисной учётки
и пользовательскому токену не виден — та же причина, что у writer'а и SIRTEP.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from fastapi import HTTPException
from loguru import logger

from app.clients.urban_api_client import UrbanApiClient
from app.common.auth.service_token import ServiceTokenProvider


@dataclass
class ScoresResult:
    """Что успели дождаться. `timed_out` не делает результат ошибкой — сводка выйдет частичной."""

    values: list[dict[str, Any]] = field(default_factory=list)  # строки индикаторов как есть — для карты/таблицы
    arrived_ids: list[int] = field(default_factory=list)
    missing_ids: list[int] = field(default_factory=list)
    timed_out: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.arrived_ids) and not self.missing_ids


class ScoreWatcher:
    """Опрашивает Urban API, пока по сценарию не появятся оценки, посчитанные сторонними сервисами."""

    def __init__(
        self,
        urban_client: UrbanApiClient,
        token_provider: ServiceTokenProvider,
        *,
        expected_ids: Sequence[int] = (),
        timeout_seconds: int = 600,
        poll_seconds: int = 10,
        quiescence_polls: int = 2,
    ):
        self._urban = urban_client
        self._tokens = token_provider
        self._expected_ids = tuple(expected_ids)
        self._timeout = timeout_seconds
        self._poll_seconds = max(poll_seconds, 1)
        # Сколько опросов подряд без прироста считать «расчёт закончился», когда явного набора нет.
        self._quiescence_polls = max(quiescence_polls, 1)

    async def await_scores(
        self,
        *,
        scenario_id: int,
        since: str | None = None,
        expected_ids: Sequence[int] | None = None,
        timeout_seconds: int | None = None,
    ) -> ScoresResult:
        """Ждёт свежие значения индикаторов-оценок у сценария.

        `since` — момент публикации в ISO: берём только строки новее него, чтобы не
        принять за оценку значение, лежавшее там до расчёта. `expected_ids`
        переопределяет набор на прогон; пустой набор включает фолбэк по «затиханию»
        (готово, когда набор свежих оценок перестаёт расти).
        """
        expected = tuple(expected_ids) if expected_ids is not None else self._expected_ids
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout

        stable_repeats = 0
        previous_ids: frozenset[int] = frozenset()
        last: ScoresResult = ScoresResult(missing_ids=list(expected))

        while True:
            fresh = await self._poll(scenario_id, since, expected)
            arrived = frozenset(row_id for row_id, _ in fresh)
            last = ScoresResult(
                values=[row for _, row in fresh],
                arrived_ids=sorted(arrived),
                missing_ids=sorted(set(expected) - arrived),
            )

            if expected:
                if not last.missing_ids:
                    return last  # дождались всего ожидаемого набора
            else:
                # Явного набора нет: ждём, пока приход не «затихнет».
                stable_repeats = stable_repeats + 1 if arrived == previous_ids else 0
                previous_ids = arrived
                if arrived and stable_repeats >= self._quiescence_polls:
                    return last

            if time.monotonic() >= deadline:
                last.timed_out = True
                logger.warning(
                    "Оценки сценария {} не досчитались за {} с: пришли {}, ждали {}",
                    scenario_id,
                    timeout,
                    last.arrived_ids,
                    list(expected),
                )
                return last

            await asyncio.sleep(self._poll_seconds)

    async def _poll(
        self,
        scenario_id: int,
        since: str | None,
        expected: Sequence[int],
    ) -> list[tuple[int, dict[str, Any]]]:
        """Свежие строки индикаторов: `[(indicator_id, row)]`. 5xx трактуем как «ещё считается»."""
        try:
            raw = await self._urban.get_scenario_indicators(scenario_id, await self._token(), expected or None)
        except HTTPException as exc:
            if exc.status_code < 500:
                raise
            logger.debug("indicators_values сценария {} ответил {} — ещё считается", scenario_id, exc.status_code)
            return []

        rows = self._urban.latest_rows_by_indicator(raw, expected or None)
        return [(row_id, row) for row_id, row in rows.items() if _is_fresh(row, since)]

    async def _token(self) -> str:
        return await self._tokens.get_token()


def _is_fresh(row: dict[str, Any], since: str | None) -> bool:
    """Строка новее момента публикации. Без `since` считаем свежим всё — сравнение ISO-строк, как в latest_rows."""
    if since is None:
        return True
    stamp = str(row.get("updated_at") or row.get("created_at") or "")
    return stamp > since
