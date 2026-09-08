"""Показатели сценария в виде, пригодном для показа человеку.

Названия, единицы измерения и нумерацию разделов Urban API отдаёт вместе со значениями
(`indicator.name_full`, `measurement_unit`, `list_label`), поэтому своей таблицы показателей
здесь нет — она бы неизбежно разошлась с справочником стенда. Наше — только порядок:
короткий «паспорт территории» вперёд, полная таблица по разделам следом.
"""

from typing import Any, Iterable

from app.clients.urban_api_client import UrbanApiClient
from app.common.constants.pipeline_constants import (
    HIGHLIGHT_INDICATOR_IDS,
    INDICATOR_GROUP_ORDER,
    INDICATOR_GROUP_TITLES,
    UNGROUPED_SECTION,
    UNGROUPED_SECTION_TITLE,
)

# Единицы, которые в справочнике означают «безразмерно».
_EMPTY_UNITS = frozenset({"", "-", "—"})
_THIN_SPACE = " "


def build_overview(
    raw_values: Iterable[dict[str, Any]],
    groups: Iterable[dict[str, Any]] | None = None,
    rows_by_indicator: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Собирает витрину: `highlights` для текста ответа, `sections` для таблицы.

    `rows_by_indicator` — уже отобранные строки (по одной на индикатор); если его нет,
    отбор делается здесь же, чтобы модуль можно было звать и на сыром ответе.
    """
    if rows_by_indicator is None:
        rows_by_indicator = UrbanApiClient.latest_rows_by_indicator(raw_values)

    section_by_indicator = section_index(groups or [])
    entries = {indicator_id: _entry(row) for indicator_id, row in rows_by_indicator.items()}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for indicator_id, entry in entries.items():
        slug = section_by_indicator.get(indicator_id, UNGROUPED_SECTION)
        buckets.setdefault(slug, []).append(entry)

    sections = [
        {
            "group": slug,
            "title": INDICATOR_GROUP_TITLES.get(slug, UNGROUPED_SECTION_TITLE if slug == UNGROUPED_SECTION else slug),
            "rows": sorted(rows, key=lambda entry: _label_sort_key(entry["label"])),
        }
        for slug, rows in sorted(buckets.items(), key=lambda item: _section_sort_key(item[0]))
    ]

    highlights = [entries[indicator_id] for indicator_id in HIGHLIGHT_INDICATOR_IDS if indicator_id in entries]
    return {"total": len(entries), "highlights": highlights, "sections": sections}


def section_index(groups: Iterable[dict[str, Any]]) -> dict[int, str]:
    """Индикатор -> раздел. Индикатор состоит в нескольких группах, берём первую по порядку."""
    index: dict[int, str] = {}
    for group in groups:
        slug = str(group.get("name") or "")
        if not slug:
            continue
        for indicator in group.get("indicators") or []:
            indicator_id = indicator.get("indicator_id")
            if not isinstance(indicator_id, int) or isinstance(indicator_id, bool):
                continue
            current = index.get(indicator_id)
            if current is None or _section_sort_key(slug) < _section_sort_key(current):
                index[indicator_id] = slug
    return index


def format_value(value: float | None, unit: str | None) -> str:
    """Число в вид для чтения: разряды тонкими пробелами, лишние нули убраны."""
    if value is None:
        return "—"
    rounded = round(value, 2)
    if rounded == int(rounded):
        text = f"{int(rounded):,}".replace(",", _THIN_SPACE)
    else:
        text = f"{rounded:,.2f}".replace(",", _THIN_SPACE).rstrip("0").rstrip(".")
    return f"{text} {unit}" if unit else text


def highlights_table(highlights: list[dict[str, Any]]) -> str:
    """Markdown-таблица для текста ответа. Пустой список — пустая строка, а не пустая шапка."""
    if not highlights:
        return ""
    lines = ["| Показатель | Значение |", "|---|---|"]
    lines += [f"| {entry['name']} | {format_value(entry['value'], entry['unit'])} |" for entry in highlights]
    return "\n".join(lines)


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    indicator = row.get("indicator") or {}
    unit = ((indicator.get("measurement_unit") or {}).get("name") or "").strip()
    value = row.get("value")
    return {
        "indicator_id": indicator.get("indicator_id"),
        "label": str(indicator.get("list_label") or ""),
        "name": indicator.get("name_full") or f"Показатель {indicator.get('indicator_id')}",
        "value": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
        "unit": None if unit in _EMPTY_UNITS else unit,
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def _section_sort_key(slug: str) -> tuple[int, str]:
    """Незнакомая группа уходит в конец, но перед разделом «Прочие показатели»."""
    if slug in INDICATOR_GROUP_ORDER:
        return (INDICATOR_GROUP_ORDER.index(slug), slug)
    return (len(INDICATOR_GROUP_ORDER) + (1 if slug == UNGROUPED_SECTION else 0), slug)


def _label_sort_key(label: str) -> tuple[tuple[int, int, str], ...]:
    """`1.2.10` идёт после `1.2.9`, а буквенные метки (`П.7`, `K.1`) — после числовых."""
    parts = []
    for part in label.split("."):
        part = part.strip()
        parts.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(parts)
