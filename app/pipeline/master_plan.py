"""Справка по прогону: застройка, публикация, очерёдность строительства.

Сырые ответы SIRTEP уходят фронтенду как есть — по ним раскрашивается карта.
Здесь из них собирается то, что читает человек: за сколько периодов всё построено,
сколько домов и сервисов в каждом, сколько жителей обеспечено сервисами и у каких
типов сервисов обеспеченность так и осталась нулевой.
"""

from typing import Any, Sequence

from app.pipeline.indicators_view import format_value

PROVISION_BRANCH = "provision"
PRIORITY_BRANCH = "priority"

_PER_PERIOD_FIELDS: dict[str, str] = {
    "houses": "houses_per_period",
    "services": "services_per_period",
    "houses_area_m2": "houses_area_per_period",
    "services_area_m2": "services_area_per_period",
    "provided": "provided_per_period",
}


def schedule_digest(answer: dict[str, Any] | None) -> dict[str, Any]:
    """Очередь строительства в человеческом виде.

    Ветку `simple` не разбираем: она приходит только для профилей 3–7 и 9, а мы
    всегда просим 1. Если пришла именно она — сообщаем ветку и не выдумываем цифр.
    """
    branch = (answer or {}).get(PROVISION_BRANCH)
    if not isinstance(branch, dict):
        return {"branch": PRIORITY_BRANCH, "by_period": [], "comments": []}

    columns = {key: _sequence(branch.get(field)) for key, field in _PER_PERIOD_FIELDS.items()}
    periods = _sequence(branch.get("periods"))
    length = max((len(column) for column in [*columns.values(), periods]), default=0)

    rows = [
        {
            "period": _at(periods, index, default=index + 1),
            **{key: _at(column, index) for key, column in columns.items()},
        }
        for index in range(length)
    ]
    built = [row for row in rows if _number(row["houses"]) or _number(row["services"])]

    return {
        "branch": PROVISION_BRANCH,
        "periods_planned": length,
        "periods_used": built[-1]["period"] if built else None,
        "houses_total": round(sum(_number(row["houses"]) for row in rows)),
        "services_total": round(sum(_number(row["services"]) for row in rows)),
        "houses_area_m2": round(sum(_number(row["houses_area_m2"]) for row in rows)),
        "services_area_m2": round(sum(_number(row["services_area_m2"]) for row in rows)),
        "provided_final": _last_known(columns["provided"]),
        "by_period": rows,
        "comments": [
            str(comment)
            for comment in (branch.get("buildings_comment"), branch.get("services_comment"))
            if isinstance(comment, str) and comment.strip()
        ],
    }


def provision_digest(answer: dict[str, Any] | None) -> dict[str, Any]:
    """ТЭПы по периодам: показатели застройки и обеспеченность по типам сервисов.

    Ключи `provision` самоописательны и приходят от SIRTEP уже по-русски: наряду с
    «Обеспеченность X (%)» там лежат жилая площадь, число домов и число жителей.
    `unbuilt_services` — типы сервисов, у которых обеспеченность равна нулю во всех
    периодах; построены они при этом могли быть.
    """
    payload = answer or {}
    by_period = [row for row in (payload.get("provision") or []) if isinstance(row, dict)]
    final = by_period[-1] if by_period else {}
    return {
        "periods": [value for value in (payload.get("periods") or []) if isinstance(value, int)],
        "final_by_service": {
            str(name): float(value)
            for name, value in sorted(final.items())
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        },
        "unbuilt_services": [str(name) for name in (payload.get("unbuilt_services") or [])],
        "by_period": by_period,
    }


def scores_digest(scores: dict[str, Any] | None) -> dict[str, Any]:
    """Оценки сторонних сервисов в человеческом виде: сколько пришло, чего не хватает.

    Скелет: пока ожидаемый набор не подтверждён живым прогоном, разбираем строки индикаторов
    по факту — id, имя и значение каждой пришедшей оценки. Форму строки уточним по реальному
    ответу `indicators_values`.
    """
    payload = scores or {}
    values = [row for row in (payload.get("values") or []) if isinstance(row, dict)]
    return {
        "arrived": len(values),
        "missing_ids": [value for value in (payload.get("missing_ids") or []) if isinstance(value, int)],
        "items": [_score_item(row) for row in values],
    }


def _score_item(row: dict[str, Any]) -> dict[str, Any]:
    indicator = row.get("indicator") if isinstance(row.get("indicator"), dict) else {}
    return {
        "indicator_id": indicator.get("indicator_id"),
        "name": indicator.get("name_full") or indicator.get("name"),
        "value": row.get("value"),
    }


def build_summary(
    *,
    buildings: dict[str, Any] | None,
    published: dict[str, Any] | None,
    schedule: dict[str, Any] | None,
    provision: dict[str, Any] | None,
    scores: dict[str, Any] | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Всё, что известно о прогоне к его концу. Любая часть может отсутствовать."""
    return {
        "buildings": buildings or {},
        "published": published,
        "schedule": schedule_digest(schedule) if schedule is not None else None,
        "provision": provision_digest(provision) if provision is not None else None,
        "scores": scores_digest(scores) if scores is not None else None,
        "warnings": list(warnings),
    }


def summary_text(summary: dict[str, Any]) -> str:
    """Текст справки для чата. Пустых разделов не пишем — только то, что реально есть."""
    blocks = [_buildings_block(summary.get("buildings") or {}), _schedule_block(summary)]
    return "\n\n".join(block for block in blocks if block)


def _buildings_block(buildings: dict[str, Any]) -> str:
    if not buildings.get("buildings"):
        return ""
    rows = [
        ("Зданий", buildings.get("buildings"), None),
        ("Жителей", buildings.get("residents"), "чел."),
        ("Жилая площадь", buildings.get("living_area_m2"), "м²"),
        ("Площадь застройки", buildings.get("building_area_m2"), "м²"),
        ("Сервисов", len(buildings.get("services") or []) or None, "типов"),
    ]
    lines = ["**Что построено**", "", "| Показатель | Значение |", "|---|---|"]
    lines += [f"| {name} | {format_value(float(value), unit)} |" for name, value, unit in rows if value]
    return "\n".join(lines)


def _schedule_block(summary: dict[str, Any]) -> str:
    schedule = summary.get("schedule")
    if not isinstance(schedule, dict) or schedule.get("branch") != PROVISION_BRANCH:
        return ""

    lines = ["**Очерёдность строительства**"]
    used, planned = schedule.get("periods_used"), schedule.get("periods_planned")
    if used and planned:
        lines.append(f"Всё построено за {used} из {planned} периодов.")
    elif not used:
        lines.append("За отведённые периоды не построено ничего.")

    provided = schedule.get("provided_final")
    if isinstance(provided, (int, float)) and not isinstance(provided, bool):
        lines.append(f"Обеспечено сервисами жителей к концу стройки — {format_value(float(provided), None)}.")

    provision = summary.get("provision")
    if isinstance(provision, dict):
        lines += _provision_lines(provision)

    lines += list(schedule.get("comments") or [])
    return "\n".join(lines)


def _provision_lines(provision: dict[str, Any]) -> list[str]:
    lines = []
    final = provision.get("final_by_service") or {}
    if final:
        listed = ", ".join(f"{name} — {format_value(value, None)}" for name, value in final.items())
        lines.append(f"Показатели к концу стройки: {listed}.")
    unbuilt = provision.get("unbuilt_services") or []
    if unbuilt:
        lines.append(f"Нулевая обеспеченность за все периоды: {', '.join(unbuilt)}.")
    return lines


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _at(column: Sequence[Any], index: int, default: Any = None) -> Any:
    value = column[index] if index < len(column) else None
    return default if value is None else value


def _last_known(column: Sequence[Any]) -> Any:
    for value in reversed(list(column)):
        if value is not None:
            return value
    return None


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
