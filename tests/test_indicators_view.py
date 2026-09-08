"""Витрина показателей: разделы, порядок, формат чисел."""

from typing import Any

from app.pipeline.indicators_view import build_overview, format_value, highlights_table, section_index


def _row(indicator_id: int, name: str, label: str, value: float, unit: str | None = None, **extra) -> dict[str, Any]:
    """Форма `ScenarioIndicatorValue` с вложенным `ShortIndicatorInfo`."""
    return {
        "indicator": {
            "indicator_id": indicator_id,
            "name_full": name,
            "list_label": label,
            "measurement_unit": {"name": unit} if unit else None,
        },
        "hexagon_id": extra.get("hexagon_id"),
        "value": value,
        "updated_at": extra.get("updated_at", "2025-01-01T00:00:00Z"),
    }


GROUPS = [
    {"name": "regional", "indicators": [{"indicator_id": 1}, {"indicator_id": 4}]},
    {"name": "demogrphy", "indicators": [{"indicator_id": 1}, {"indicator_id": 37}]},
    {"name": "transport", "indicators": [{"indicator_id": 60}]},
]

VALUES = [
    _row(1, "Численность населения", "2.1", 1234567, "человек"),
    _row(37, "Плотность населения", "2.2", 812.5, "чел/км2"),
    _row(4, "Площадь территории", "1.1", 1520.0, "км2"),
    _row(60, "Плотность улично-дорожной сети", "3.1.3", 2.4, "км/км2"),
    _row(271, "Потенциал развития жилой застройки типа ИЖС", "1.5.9.1.1", 0.42),
]


def test_indicator_lands_in_its_thematic_section():
    """Показатель состоит и в `regional`, и в `demogrphy` — в таблице он должен быть один раз."""
    index = section_index(GROUPS)
    assert index[1] == "demogrphy"
    assert index[4] == "regional"


def test_sections_are_ordered_and_ungrouped_go_last():
    overview = build_overview(VALUES, GROUPS)
    assert [section["group"] for section in overview["sections"]] == [
        "demogrphy",
        "transport",
        "regional",
        "other",
    ]
    assert overview["sections"][-1]["title"] == "Прочие показатели"


def test_section_titles_are_human_readable():
    overview = build_overview(VALUES, GROUPS)
    assert overview["sections"][0]["title"] == "Демография"


def test_highlights_follow_the_passport_order_not_the_response_order():
    """Численность -> плотность -> площадь, независимо от того, как ответил Urban API."""
    overview = build_overview(VALUES, GROUPS)
    assert [entry["indicator_id"] for entry in overview["highlights"]] == [1, 37, 4, 60]


def test_names_and_units_come_from_the_api():
    """Своего справочника показателей у сервиса нет — он бы разошёлся со стендом."""
    overview = build_overview(VALUES, GROUPS)
    population = overview["highlights"][0]
    assert population["name"] == "Численность населения"
    assert population["unit"] == "человек"


def test_unitless_dash_is_not_shown_as_a_unit():
    overview = build_overview([_row(1, "Рождаемость", "2.4", 8.1, "-")], [])
    assert overview["sections"][0]["rows"][0]["unit"] is None


def test_territory_value_wins_over_a_hexagon_one():
    values = [
        _row(1, "Численность населения", "2.1", 42, "человек", hexagon_id=7),
        _row(1, "Численность населения", "2.1", 1234567, "человек"),
    ]
    overview = build_overview(values, GROUPS)
    assert overview["highlights"][0]["value"] == 1234567


def test_rows_are_sorted_naturally_by_label():
    """`1.2.10` идёт после `1.2.9`, а не между `1.2.1` и `1.2.2`."""
    values = [
        _row(265, "Малые сельские поселения", "1.2.10", 5, "ед"),
        _row(12, "Малые населенные пункты", "1.2.6", 12, "ед"),
        _row(264, "Средние сельские поселения", "1.2.9", 3, "ед"),
    ]
    overview = build_overview(values, [])
    assert [row["label"] for row in overview["sections"][0]["rows"]] == ["1.2.6", "1.2.9", "1.2.10"]


def test_total_counts_indicators_not_rows():
    values = VALUES + [_row(1, "Численность населения", "2.1", 1, "человек", hexagon_id=9)]
    assert build_overview(values, GROUPS)["total"] == 5


def test_format_value_groups_digits_and_drops_trailing_zeros():
    assert format_value(1234567.0, "человек").replace(" ", " ") == "1 234 567 человек"
    assert format_value(812.50, "чел/км2") == "812.5 чел/км2"
    assert format_value(None, "человек") == "—"


def test_highlights_table_is_empty_when_there_is_nothing_to_show():
    assert highlights_table([]) == ""


def test_highlights_table_has_a_header_and_a_row_per_indicator():
    table = highlights_table(build_overview(VALUES, GROUPS)["highlights"])
    assert table.splitlines()[0] == "| Показатель | Значение |"
    assert len(table.splitlines()) == 6
