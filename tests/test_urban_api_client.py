"""Разбор ответа `indicators_values`. Схема сверена с OpenAPI стенда."""

from app.clients.urban_api_client import UrbanApiClient

IDS = (271, 274, 280)


def _row(indicator_id: int, value, **extra) -> dict:
    """Форма `ScenarioIndicatorValue`: indicator_id вложен, даты — updated_at/created_at."""
    return {
        "indicator_value_id": 1,
        "indicator": {"indicator_id": indicator_id, "name_full": "…", "level": 4},
        "scenario": {"id": 1, "name": "…"},
        "territory": {"id": 1, "name": "…"},
        "hexagon_id": None,
        "value": value,
        "information_source": "modeled",
        **extra,
    }


def test_indicator_id_is_read_from_nested_object():
    assert UrbanApiClient.latest_values_by_indicator([_row(271, 5)], IDS) == {271: 5.0}


def test_freshest_by_updated_at_wins():
    values = UrbanApiClient.latest_values_by_indicator(
        [
            _row(274, 0.1, updated_at="2024-01-01T00:00:00Z"),
            _row(274, 0.8, updated_at="2025-06-01T00:00:00Z"),
        ],
        IDS,
    )
    assert values == {274: 0.8}


def test_territory_value_beats_hexagon_value():
    """Гексагональное значение описывает одну ячейку, территориальное — всю территорию."""
    values = UrbanApiClient.latest_values_by_indicator(
        [
            _row(280, 9.0, hexagon_id=None, updated_at="2024-01-01T00:00:00Z"),
            _row(280, 1.0, hexagon_id=42, updated_at="2025-06-01T00:00:00Z"),
        ],
        IDS,
    )
    assert values == {280: 9.0}


def test_hexagon_value_used_when_nothing_else_exists():
    values = UrbanApiClient.latest_values_by_indicator([_row(280, 1.0, hexagon_id=42)], IDS)
    assert values == {280: 1.0}


def test_freshest_hexagon_wins_among_hexagons():
    values = UrbanApiClient.latest_values_by_indicator(
        [
            _row(280, 1.0, hexagon_id=42, updated_at="2024-01-01T00:00:00Z"),
            _row(280, 2.0, hexagon_id=43, updated_at="2025-06-01T00:00:00Z"),
        ],
        IDS,
    )
    assert values == {280: 2.0}


def test_foreign_indicators_are_ignored():
    assert UrbanApiClient.latest_values_by_indicator([_row(999, 100)], IDS) == {}


def test_null_values_are_dropped():
    assert UrbanApiClient.latest_values_by_indicator([_row(280, None)], IDS) == {}


def test_string_values_are_parsed():
    assert UrbanApiClient.latest_values_by_indicator([_row(280, "3,5")], IDS) == {280: 3.5}


def test_flat_indicator_id_still_understood():
    """Плоская форма встречается в других ручках Urban API — не спотыкаемся о неё."""
    assert UrbanApiClient.latest_values_by_indicator([{"indicator_id": 271, "value": 2}], IDS) == {271: 2.0}
