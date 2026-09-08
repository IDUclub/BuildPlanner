"""Площадь GeoJSON без geopandas. Сверяемся с величинами, которые считаются вручную."""

import pytest

from app.pipeline.geo_area import feature_collection_area_by_key, geometry_area_m2

# Квадрат 0.01° по обеим осям около экватора: ~1.1132 км по долготе, столько же по широте.
EQUATOR_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
}

# Тот же квадрат на широте 60°: по долготе он вдвое уже (cos 60° = 0.5).
NORTHERN_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[30.0, 60.0], [30.01, 60.0], [30.01, 60.01], [30.0, 60.01], [30.0, 60.0]]],
}


def test_equatorial_square_matches_hand_calculation():
    """0.01° ≈ 1113.2 м, значит около 1.239 км²."""
    assert geometry_area_m2(EQUATOR_SQUARE) == pytest.approx(1_239_000, rel=0.01)


def test_area_shrinks_with_latitude():
    """На 60° градус долготы вдвое короче — площадь примерно вдвое меньше."""
    ratio = geometry_area_m2(NORTHERN_SQUARE) / geometry_area_m2(EQUATOR_SQUARE)
    assert ratio == pytest.approx(0.5, rel=0.02)


def test_winding_order_does_not_matter():
    reversed_ring = {"type": "Polygon", "coordinates": [list(reversed(EQUATOR_SQUARE["coordinates"][0]))]}
    assert geometry_area_m2(reversed_ring) == pytest.approx(geometry_area_m2(EQUATOR_SQUARE))


def test_unclosed_ring_is_closed_implicitly():
    unclosed = {"type": "Polygon", "coordinates": [EQUATOR_SQUARE["coordinates"][0][:-1]]}
    assert geometry_area_m2(unclosed) == pytest.approx(geometry_area_m2(EQUATOR_SQUARE))


def test_holes_are_subtracted():
    hole = [[0.002, 0.002], [0.008, 0.002], [0.008, 0.008], [0.002, 0.008], [0.002, 0.002]]
    with_hole = {"type": "Polygon", "coordinates": [EQUATOR_SQUARE["coordinates"][0], hole]}
    assert geometry_area_m2(with_hole) < geometry_area_m2(EQUATOR_SQUARE)
    assert geometry_area_m2(with_hole) == pytest.approx(geometry_area_m2(EQUATOR_SQUARE) * 0.64, rel=0.02)


def test_multipolygon_sums_parts():
    multi = {"type": "MultiPolygon", "coordinates": [EQUATOR_SQUARE["coordinates"], EQUATOR_SQUARE["coordinates"]]}
    assert geometry_area_m2(multi) == pytest.approx(geometry_area_m2(EQUATOR_SQUARE) * 2)


@pytest.mark.parametrize(
    "geometry",
    [None, {}, {"type": "Point", "coordinates": [30.0, 60.0]}, {"type": "Polygon", "coordinates": []}, "жила"],
)
def test_unsupported_geometry_is_zero(geometry):
    """Чужой GeoJSON бывает грязным — падать на нём нельзя."""
    assert geometry_area_m2(geometry) == 0.0


def test_degenerate_ring_is_zero():
    assert geometry_area_m2({"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.01, 0.0]]]}) == 0.0


def test_area_is_grouped_by_property():
    features = [
        {"geometry": EQUATOR_SQUARE, "properties": {"zone": "residential"}},
        {"geometry": EQUATOR_SQUARE, "properties": {"zone": "residential"}},
        {"geometry": EQUATOR_SQUARE, "properties": {"zone": "industrial"}},
        {"geometry": EQUATOR_SQUARE, "properties": {}},
    ]
    areas = feature_collection_area_by_key(features, "zone")
    assert set(areas) == {"residential", "industrial"}
    assert areas["residential"] == pytest.approx(areas["industrial"] * 2)
