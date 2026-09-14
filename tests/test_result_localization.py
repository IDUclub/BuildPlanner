"""Атрибуты зон и дорог, которые видит пользователь на карте."""

import copy

import pytest

from app.pipeline.result_localization import localize_road_properties, localize_roads, localize_zones, zone_name


def _collection(*properties):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": index,
                "geometry": {"type": "Point", "coordinates": [30, 60]},
                "properties": props,
            }
            for index, props in enumerate(properties)
        ],
    }


def test_generated_zone_is_shown_by_russian_name_only():
    zones = localize_zones(
        _collection(
            {
                "territory_zone": 7,
                "territory_zone_name": "business",
                "is_generated": True,
                "functional_zone_id": None,
                "functional_zone_type_id": None,
            }
        )
    )
    assert zones["features"][0]["properties"] == {"Территориальная зона": "общественно-деловая", "Сгенерирована": "Да"}


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"territory_zone_name": "Industrial"}, "промышленная"),
        ({"territory_zone": 12}, "жилая"),
        ({"territory_zone": "6"}, "транспортная"),
        ({"territory_zone_name": "moon", "territory_zone": 3}, "специального назначения"),
        ({"territory_zone": 99}, "не определена"),
        ({}, "не определена"),
    ],
)
def test_zone_name_falls_back_from_kind_to_id(properties, expected):
    assert zone_name(properties) == expected


def test_existing_zone_keeps_its_source_id_and_drops_the_urban_record():
    zones = localize_zones(
        _collection({"territory_zone": 2, "is_generated": False, "functional_zone_id": 501, "year": 2024})
    )
    assert zones["features"][0]["properties"] == {
        "Территориальная зона": "рекреационная",
        "Сгенерирована": "Нет",
        "Идентификатор исходной зоны": 501,
    }


@pytest.mark.parametrize(
    ("road_lvl", "road_class"),
    [
        ("regulated highway", "highway"),
        ("local road, level 2", "street"),
        ("  Local   Road, level 1", "street"),
        ("user_roads", "existing"),
    ],
)
def test_road_keeps_level_and_width_and_gets_a_legend_class(road_lvl, road_class):
    assert localize_road_properties({"road_lvl": road_lvl, "roads_width": 6.0}) == {
        "Ширина, м": 6.0,
        "road_lvl": road_lvl,
        "road_class": road_class,
    }


def test_unknown_road_level_gets_no_invented_class():
    assert localize_road_properties({"road_lvl": "footpath"}) == {"road_lvl": "footpath"}


def test_existing_road_keeps_its_name_address_and_type():
    properties = {"name": "Невский проспект", "address": "СПб", "physical_object_type_id": 52, "object_id": 1}
    assert localize_road_properties(properties) == {
        "Название": "Невский проспект",
        "Адрес": "СПб",
        "physical_object_type_id": 52,
    }


def test_localization_does_not_touch_the_source_or_geometry():
    raw = _collection({"territory_zone": 4, "territory_zone_name": "industrial"})
    snapshot = copy.deepcopy(raw)
    zones = localize_zones(raw)

    assert raw == snapshot
    assert zones["features"][0]["geometry"] == raw["features"][0]["geometry"]
    assert zones["features"][0]["id"] == 0


@pytest.mark.parametrize("collection", [None, {"type": "FeatureCollection"}, [1, 2]])
def test_malformed_collection_is_passed_through(collection):
    assert localize_roads(collection) == collection


def test_feature_without_properties_still_gets_a_zone_label():
    zones = localize_zones({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": None}]})
    assert zones["features"][0]["properties"] == {"Территориальная зона": "не определена"}
