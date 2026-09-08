import pytest

from app.common.constants.pipeline_constants import (
    COVERAGE_RATIO_CAP,
    DENSITY_SCENARIOS,
    GENPLANNER_TO_GENBUILDER_ZONE,
    PROFILE_TARGETS,
    RATE_TO_VOLUME_TARGET,
    RESIDENTS_PER_HECTARE_CAP,
)

TEN_HECTARES = 100_000.0  # м²
from app.pipeline.targets_policy import (
    FALLBACK_TARGETS_BY_ZONE,
    FLOORS_CAP_BY_GROUP,
    build_targets_by_zone,
    to_genbuilder_targets,
    zones_without_volume_target,
)


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


# --- форма для GenBuilder ---------------------------------------------------------------


def test_wire_form_is_parameter_major():
    """GenBuilder читает `targets_by_zone["floors_avg"]["residential"]`, а не наоборот."""
    wire = to_genbuilder_targets(build_targets_by_zone(13, {"residential", "industrial"}))
    assert wire["floors_avg"]["residential"] == 16
    assert wire["floors_avg"]["industrial"] == 2
    assert wire["density_scenario"]["residential"] == "max"


def test_wire_form_drops_unknown_parameters():
    """Своих ключей GenBuilder не понимает — в тело они попасть не должны."""
    wire = to_genbuilder_targets({"residential": {"floors_avg": 8, "source_profile_id": 12}})
    assert set(wire) == {"floors_avg"}


def test_wire_form_of_empty_targets_is_empty():
    assert to_genbuilder_targets({}) == {}


# --- цель объёма ------------------------------------------------------------------------


@pytest.mark.parametrize("profile_id", sorted(PROFILE_TARGETS))
def test_every_profile_has_a_volume_target(profile_id):
    """Без `residents`/`coverage_area` GenBuilder пропускает ветку генерации целиком."""
    mapping = GENPLANNER_TO_GENBUILDER_ZONE[profile_id]
    assert mapping is not None
    zone = mapping[0]
    targets = build_targets_by_zone(profile_id, {zone}, {zone: TEN_HECTARES})
    assert zones_without_volume_target(targets) == []


@pytest.mark.parametrize("zone", sorted(FALLBACK_TARGETS_BY_ZONE))
def test_every_fallback_zone_has_a_volume_target(zone):
    """Профиль 2 не застраивается, поэтому все зоны идут по запасному варианту."""
    targets = build_targets_by_zone(2, {zone}, {zone: TEN_HECTARES})
    assert zones_without_volume_target(targets) == []


def test_zone_stripped_of_its_volume_target_is_reported():
    targets = build_targets_by_zone(
        13, {"residential"}, {"residential": TEN_HECTARES}, overrides={"residential": {"residents": 0}}
    )
    assert zones_without_volume_target(targets) == ["residential"]


def test_zone_without_area_gets_no_volume_target():
    """Нулевая площадь честнее случайного числа: зона попадёт в предупреждение."""
    targets = build_targets_by_zone(13, {"residential"}, {"residential": 0.0})
    assert "residents" not in targets["residential"]
    assert zones_without_volume_target(targets) == ["residential"]


# --- цели от площади --------------------------------------------------------------------


def test_residents_scale_with_area():
    """420 чел/га у многоэтажной застройки на 10 га — 4200 человек."""
    targets = build_targets_by_zone(13, {"residential"}, {"residential": TEN_HECTARES})
    assert targets["residential"]["residents"] == 4200


def test_izhs_gets_far_fewer_residents_on_the_same_area():
    izhs = build_targets_by_zone(10, {"residential"}, {"residential": TEN_HECTARES})
    highrise = build_targets_by_zone(13, {"residential"}, {"residential": TEN_HECTARES})
    assert izhs["residential"]["residents"] < highrise["residential"]["residents"] / 5


def test_coverage_area_is_a_share_of_the_zone_area():
    """Промзона профиля: 45% площади блоков."""
    targets = build_targets_by_zone(4, {"industrial"}, {"industrial": TEN_HECTARES})
    assert targets["industrial"]["coverage_area"] == round(TEN_HECTARES * 0.45)


def test_double_the_area_doubles_the_target():
    small = build_targets_by_zone(4, {"industrial"}, {"industrial": TEN_HECTARES})
    large = build_targets_by_zone(4, {"industrial"}, {"industrial": TEN_HECTARES * 2})
    assert large["industrial"]["coverage_area"] == small["industrial"]["coverage_area"] * 2


def test_rates_never_reach_genbuilder():
    """`residents_per_ha` и `coverage_ratio` — внутренние ключи оркестратора."""
    targets = build_targets_by_zone(13, {"residential"}, {"residential": TEN_HECTARES})
    assert set(targets["residential"]) & set(RATE_TO_VOLUME_TARGET) == set()


# --- «в разумных пределах» --------------------------------------------------------------


def test_density_override_is_capped_by_floor_group():
    targets = build_targets_by_zone(
        10, {"residential"}, {"residential": TEN_HECTARES}, overrides={"residential": {"residents_per_ha": 5000}}
    )
    assert targets["residential"]["residents"] == RESIDENTS_PER_HECTARE_CAP["private"] * 10


def test_coverage_override_is_capped():
    targets = build_targets_by_zone(
        4, {"industrial"}, {"industrial": TEN_HECTARES}, overrides={"industrial": {"coverage_ratio": 5}}
    )
    assert targets["industrial"]["coverage_area"] == round(TEN_HECTARES * COVERAGE_RATIO_CAP)


def test_explicit_absolute_override_wins_over_the_area_estimate():
    """Пользователь назвал число людей — считаем по нему, а не по плотности."""
    targets = build_targets_by_zone(
        13, {"residential"}, {"residential": TEN_HECTARES}, overrides={"residential": {"residents": 100}}
    )
    assert targets["residential"]["residents"] == 100


def test_density_scenario_values_are_accepted_by_genbuilder():
    """Значение вне {min, mean, max} роняет генерацию: `Unknown FAR scenario`."""
    used = {target["density_scenario"] for target in PROFILE_TARGETS.values()}
    used |= {target["density_scenario"] for target in FALLBACK_TARGETS_BY_ZONE.values()}
    assert used <= DENSITY_SCENARIOS
