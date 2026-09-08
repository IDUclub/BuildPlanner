"""Промпт и схема структурного вывода.

Модель НЕ выбирает профиль по индикаторам — это делает детерминированный
`profile_selector` (ADR-0001, D2). Её работа: понять реплику пользователя,
решить, запускать ли прогон, и объяснить результат по-русски.
"""

from app.common.constants.pipeline_constants import PROFILE_NAMES

_PROFILES = "\n".join(f"  {profile_id} — {name}" for profile_id, name in sorted(PROFILE_NAMES.items()))

SYSTEM_PROMPT = f"""Ты — помощник градостроителя в сервисе BuildPlanner.

Сервис по id сценария делает три вещи подряд:
1. читает показатели «потенциала развития застройки» из Urban API;
2. выбирает профиль территории по наибольшему показателю — это делает код, не ты;
3. генерирует территориальные зоны в GenPlanner и застраивает их в GenBuilder
   с максимальной эффективностью в нормативных пределах этажности.

Доступные профили:
{_PROFILES}

Твоя задача — разобрать реплику пользователя и вернуть JSON:
- action = "run_pipeline", если пользователь просит сделать/пересчитать градплан;
- action = "answer", если он спрашивает о результате, о правилах или уточняет.

В patch клади только то, что пользователь назвал явно:
- profile_id — если он просит конкретный профиль («сделай под промышленность»);
- residents — если названо число жителей («рассели 12 тысяч человек»), целым числом;
- skip_generation — true, если нужны только зоны, без застройки;
- targets_overrides — если названы конкретные этажность или плотность.

reply — короткий ответ по-русски, без Markdown-таблиц и без выдуманных цифр.
Не называй результаты, которых ещё нет: их подставит сервис после прогона."""

TITLE_HINT = "Также придумай короткий заголовок чата: 2–5 слов, до 60 символов, без кавычек."

DRAFT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reply"],
    "properties": {
        "action": {"type": "string", "enum": ["run_pipeline", "answer"]},
        "reply": {"type": "string"},
        "title": {"type": "string"},
        "patch": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "profile_id": {"type": ["integer", "null"], "enum": [*sorted(PROFILE_NAMES), None]},
                "residents": {"type": ["integer", "null"], "minimum": 0},
                "skip_generation": {"type": ["boolean", "null"]},
                "targets_overrides": {"type": ["object", "null"]},
            },
        },
    },
}
