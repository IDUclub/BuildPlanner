from app.pipeline.buildings_summary import summarize_buildings


def _feature(zone: str | None, **properties) -> dict:
    return {"type": "Feature", "geometry": None, "properties": {"zone": zone, **properties}}


def test_totals_come_from_genbuilder_properties():
    summary = summarize_buildings(
        [
            _feature("residential", residents_number=61.0, living_area=1840.4, building_area=2300.0, service=[]),
            _feature("residential", residents_number=40.0, living_area=1200.0, building_area=1500.2, service=[]),
            _feature("industrial", residents_number=0.0, living_area=0.0, building_area=900.0, service=[]),
        ]
    )
    assert summary["buildings"] == 3
    assert summary["buildings_by_zone"] == {"residential": 2, "industrial": 1}
    assert summary["residents"] == 101
    assert summary["living_area_m2"] == 3040
    assert summary["building_area_m2"] == 4700


def test_services_are_grouped_by_name():
    summary = summarize_buildings(
        [
            _feature("residential", service=[{"Школа": 800.0}]),
            _feature("residential", service=[{"Школа": 600.0}, {"Детский сад": 150.4}]),
        ]
    )
    assert summary["services"] == [
        {"name": "Детский сад", "count": 1, "capacity": 150},
        {"name": "Школа", "count": 2, "capacity": 1400},
    ]


def test_missing_and_null_properties_count_as_zero():
    summary = summarize_buildings([{"type": "Feature"}, _feature(None, residents_number=None)])
    assert summary["buildings"] == 2
    assert summary["buildings_by_zone"] == {"unassigned": 2}
    assert (summary["residents"], summary["services"]) == (0, [])
