"""Выбор градостроительного профиля по показателям сценария.

Правило детерминированное и объяснимое (ADR-0001, D2): все участвующие индикаторы
принадлежат одному семейству 269 «Потенциал развития застройки» и лежат на единой
безразмерной шкале, поэтому сравниваются напрямую. Нормировка считается только
для скор-борда, который уходит пользователю событием `profile_selected`.

Семейство 284 («баллов»: 286, 287, 288) в выборе не участвует по решению заказчика.
"""

from dataclasses import dataclass, field

from app.common.constants.pipeline_constants import (
    INDICATOR_NAMES,
    INDICATOR_TO_PROFILE,
    NON_BUILDABLE_PROFILES,
    PROFILE_NAMES,
    SELECTION_INDICATOR_IDS,
)


@dataclass(frozen=True)
class ScoreEntry:
    indicator_id: int
    name: str
    profile_id: int
    raw: float
    normalized: float

    def as_dict(self) -> dict[str, object]:
        return {
            "indicator_id": self.indicator_id,
            "name": self.name,
            "profile_id": self.profile_id,
            "raw": self.raw,
            "normalized": round(self.normalized, 4),
        }


@dataclass(frozen=True)
class ProfileSelection:
    profile_id: int
    profile_name: str
    indicator_id: int
    reason: str
    scoreboard: list[ScoreEntry] = field(default_factory=list)
    missing_indicator_ids: list[int] = field(default_factory=list)

    @property
    def buildable(self) -> bool:
        return self.profile_id not in NON_BUILDABLE_PROFILES

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "indicator_id": self.indicator_id,
            "reason": self.reason,
            "buildable": self.buildable,
            "missing_indicator_ids": self.missing_indicator_ids,
            "scoreboard": [entry.as_dict() for entry in self.scoreboard],
        }


class NoIndicatorValuesError(RuntimeError):
    """Ни по одному из индикаторов выбора у сценария нет значения."""


def select_profile(values: dict[int, float]) -> ProfileSelection:
    """Возвращает профиль-победитель и полный скор-борд.

    Главный критерий — значение показателя. При равенстве значений предпочтение
    отдаётся застраиваемому профилю: не застраиваемый победитель означал бы прогон,
    в котором GenBuilder исключает часть кварталов впустую. Среди равных по
    застраиваемости выигрывает индикатор с меньшим id — так выбор воспроизводим,
    а не зависит от порядка ответа Urban API.
    """
    scored = {indicator_id: value for indicator_id, value in values.items() if indicator_id in INDICATOR_TO_PROFILE}
    if not scored:
        raise NoIndicatorValuesError(
            "У сценария нет значений ни по одному из индикаторов " f"{', '.join(map(str, SELECTION_INDICATOR_IDS))}"
        )

    lowest = min(scored.values())
    highest = max(scored.values())
    span = highest - lowest

    scoreboard = sorted(
        (
            ScoreEntry(
                indicator_id=indicator_id,
                name=INDICATOR_NAMES.get(indicator_id, str(indicator_id)),
                profile_id=INDICATOR_TO_PROFILE[indicator_id],
                raw=value,
                normalized=1.0 if span == 0 else (value - lowest) / span,
            )
            for indicator_id, value in scored.items()
        ),
        key=lambda entry: (-entry.raw, entry.profile_id in NON_BUILDABLE_PROFILES, entry.indicator_id),
    )

    winner = scoreboard[0]
    runner_up = scoreboard[1] if len(scoreboard) > 1 else None
    reason = (
        f"Наибольшее значение — «{winner.name}» ({_fmt(winner.raw)})"
        + (f", следом «{runner_up.name}» ({_fmt(runner_up.raw)})" if runner_up else "")
        + f". Профиль генерации: {PROFILE_NAMES.get(winner.profile_id, winner.profile_id)}."
    )

    return ProfileSelection(
        profile_id=winner.profile_id,
        profile_name=PROFILE_NAMES.get(winner.profile_id, str(winner.profile_id)),
        indicator_id=winner.indicator_id,
        reason=reason,
        scoreboard=scoreboard,
        missing_indicator_ids=sorted(set(SELECTION_INDICATOR_IDS) - set(scored)),
    )


def _fmt(value: float) -> str:
    return f"{value:g}"
