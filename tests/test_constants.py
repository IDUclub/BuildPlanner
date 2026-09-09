"""Полнота таблиц перевода. Рассинхрон здесь — самый частый источник ошибок пайплайна."""

from app.common.constants.pipeline_constants import (
    BUILDING_TYPE_NAME_BY_ZONE,
    DEFAULT_BUILDING_TYPE_NAME,
    GENPLANNER_TO_GENBUILDER_ZONE,
    INDICATOR_NAMES,
    INDICATOR_TO_PROFILE,
    NON_BUILDABLE_PROFILES,
    PROFILE_NAMES,
    PROFILE_TARGETS,
    SELECTION_INDICATOR_IDS,
)

GENBUILDER_ZONES = {"residential", "business", "industrial", "transport", "special", "unknown"}
GENBUILDER_FLOOR_GROUPS = {"private", "low", "medium", "high"}


def test_selection_covers_exactly_the_agreed_ten_indicators():
    assert SELECTION_INDICATOR_IDS == (271, 272, 273, 274, 275, 276, 277, 278, 279, 280)


def test_family_284_is_excluded():
    assert not {286, 287, 288} & set(INDICATOR_TO_PROFILE)


def test_every_indicator_has_a_name_and_a_known_profile():
    for indicator_id, profile_id in INDICATOR_TO_PROFILE.items():
        assert indicator_id in INDICATOR_NAMES
        assert profile_id in PROFILE_NAMES


def test_every_profile_has_a_genbuilder_mapping():
    assert set(PROFILE_NAMES) == set(GENPLANNER_TO_GENBUILDER_ZONE)


def test_mappings_use_only_genbuilder_taxonomy():
    for mapping in GENPLANNER_TO_GENBUILDER_ZONE.values():
        if mapping is None:
            continue
        zone, floors_group = mapping
        assert zone in GENBUILDER_ZONES
        assert floors_group is None or floors_group in GENBUILDER_FLOOR_GROUPS


def test_recreation_and_agriculture_are_the_only_non_buildable():
    assert NON_BUILDABLE_PROFILES == frozenset({2, 5})


def test_every_buildable_profile_has_targets():
    buildable = set(PROFILE_NAMES) - NON_BUILDABLE_PROFILES
    assert buildable == set(PROFILE_TARGETS)


def test_no_target_repeats_genbuilder_bad_defaults():
    for profile_id, target in PROFILE_TARGETS.items():
        assert target["floors_avg"] < 19, f"профиль {profile_id} унаследовал дефолт GenBuilder"
        assert target.get("default_floor_group") != "extreme"


# Снято со стенда 2026-09-09: GET /api/v1/functional_zones_types.
# Профили пайплайна живут в этом же пространстве id, поэтому при записи сценария
# зона не переводится. Тест ловит попытку завести профиль, которого Urban API не знает.
URBAN_FUNCTIONAL_ZONE_TYPE_IDS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15})


def test_every_profile_is_a_known_urban_api_zone_type():
    unknown = set(PROFILE_NAMES) - URBAN_FUNCTIONAL_ZONE_TYPE_IDS
    assert not unknown, f"Urban API не знает таких типов зон: {sorted(unknown)}"


def test_building_types_cover_every_genbuilder_zone():
    """У каждой застраиваемой зоны должен быть тип физобъекта — хотя бы дефолтный."""
    zones = {mapping[0] for mapping in GENPLANNER_TO_GENBUILDER_ZONE.values() if mapping is not None}
    assert zones - set(BUILDING_TYPE_NAME_BY_ZONE) == zones - {"residential"}
    assert DEFAULT_BUILDING_TYPE_NAME
