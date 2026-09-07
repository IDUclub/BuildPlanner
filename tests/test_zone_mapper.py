from app.pipeline.zone_mapper import map_zones_to_blocks, resolve_profile_id


def _zone(properties: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": properties}


def _collection(*properties: dict) -> dict:
    return {"type": "FeatureCollection", "features": [_zone(item) for item in properties]}


def test_residential_subprofile_carries_floors_group():
    result = map_zones_to_blocks(_collection({"territory_zone": 11}))
    block = result.blocks["features"][0]["properties"]
    assert block["zone"] == "residential"
    assert block["floors_group"] == "low"
    assert block["source_profile_id"] == 11


def test_recreation_and_agriculture_are_skipped():
    result = map_zones_to_blocks(_collection({"territory_zone": 2}, {"territory_zone": 5}, {"territory_zone": 4}))
    assert result.buildable_count == 1
    assert result.skipped_by_profile == {2: 1, 5: 1}
    assert "не застраивается" not in result.blocks["features"][0]["properties"]


def test_warning_message_counts_everything():
    result = map_zones_to_blocks(_collection({"territory_zone": 2}, {"territory_zone": 4}, {"nothing": True}))
    message = result.warning_message()
    assert message is not None
    assert "Из 3 зон застраивается 1" in message
    assert result.total_count == 3


def test_no_warning_when_everything_is_buildable():
    result = map_zones_to_blocks(_collection({"territory_zone": 4}, {"territory_zone": 7}))
    assert result.warning_message() is None
    assert result.zones_present == {"industrial", "business"}


def test_original_properties_are_preserved():
    result = map_zones_to_blocks(_collection({"territory_zone": 12, "is_generated": True}))
    assert result.blocks["features"][0]["properties"]["is_generated"] is True


def test_zone_resolved_by_russian_name_when_id_is_absent():
    assert resolve_profile_id({"territory_zone_name": "Промышленная"}) == 4
    assert resolve_profile_id({"Территориальная зона": "жилая"}) == 1


def test_unrecognized_zone_is_counted_not_dropped_silently():
    result = map_zones_to_blocks(_collection({"territory_zone_name": "чего-то новое"}))
    assert result.unrecognized_count == 1
    assert result.buildable_count == 0


def test_boolean_property_is_not_mistaken_for_zone_id():
    assert resolve_profile_id({"territory_zone": True}) is None
