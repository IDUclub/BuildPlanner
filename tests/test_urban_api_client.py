from app.clients.urban_api_client import UrbanApiClient

IDS = (271, 274, 280)


def test_latest_value_wins():
    values = UrbanApiClient.latest_values_by_indicator(
        [
            {"indicator_id": 274, "value": 0.1, "date_value": "2024-01-01"},
            {"indicator_id": 274, "value": 0.8, "date_value": "2025-06-01"},
        ],
        IDS,
    )
    assert values == {274: 0.8}


def test_nested_indicator_object_is_understood():
    values = UrbanApiClient.latest_values_by_indicator([{"indicator": {"indicator_id": 271}, "value": 5}], IDS)
    assert values == {271: 5.0}


def test_foreign_indicators_are_ignored():
    values = UrbanApiClient.latest_values_by_indicator([{"indicator_id": 999, "value": 100}], IDS)
    assert values == {}


def test_string_values_are_parsed():
    values = UrbanApiClient.latest_values_by_indicator([{"indicator_id": 280, "value": "3,5"}], IDS)
    assert values == {280: 3.5}


def test_null_values_are_dropped():
    values = UrbanApiClient.latest_values_by_indicator([{"indicator_id": 280, "value": None}], IDS)
    assert values == {}
