from app.pipeline.targets_policy import FLOORS_CAP_BY_GROUP, build_targets_by_zone


def test_profile_zone_is_built_at_maximum():
    targets = build_targets_by_zone(13, {"residential"})
    assert targets["residential"]["density_scenario"] == "max"
    assert targets["residential"]["floors_avg"] == 16


def test_other_zones_are_built_moderately():
    targets = build_targets_by_zone(13, {"residential", "industrial"})
    assert targets["industrial"]["density_scenario"] == "mean"


def test_izhs_never_gets_genbuilder_default_of_19_floors():
    targets = build_targets_by_zone(10, {"residential"})
    assert targets["residential"]["floors_avg"] == 2
    assert targets["residential"]["default_floor_group"] == "private"


def test_floors_are_clamped_to_group_ceiling():
    targets = build_targets_by_zone(12, {"residential"}, overrides={"residential": {"floors_avg": 40}})
    assert targets["residential"]["floors_avg"] == FLOORS_CAP_BY_GROUP["medium"]


def test_overrides_without_group_pass_through():
    targets = build_targets_by_zone(4, {"industrial"}, overrides={"industrial": {"coverage_area": 5000}})
    assert targets["industrial"]["coverage_area"] == 5000


def test_only_present_zones_are_addressed():
    targets = build_targets_by_zone(7, {"business"})
    assert set(targets) == {"business"}


def test_non_buildable_profile_still_yields_targets_for_present_zones():
    targets = build_targets_by_zone(2, {"residential", "business"})
    assert set(targets) == {"residential", "business"}
    assert all(target["density_scenario"] == "mean" for target in targets.values())
