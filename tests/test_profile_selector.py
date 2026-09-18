import pytest

from app.pipeline.profile_selector import NoIndicatorValuesError, select_profile


def test_picks_profile_of_the_largest_indicator():
    selection = select_profile({271: 0.1, 274: 0.9, 278: 0.5})
    assert selection.profile_id == 13  # 274 -> многоэтажная жилая
    assert selection.indicator_id == 274
    assert selection.buildable is True


def test_residential_subprofile_wins_without_unfolding_hierarchy():
    """271-274 сравниваются наравне с 275-280 и сразу дают подпрофиль 10-13."""
    selection = select_profile({271: 0.8, 275: 0.7, 278: 0.6})
    assert selection.profile_id == 10


def test_family_284_is_not_used_even_if_passed():
    selection = select_profile({276: 0.2, 287: 999.0, 288: 999.0})
    assert selection.indicator_id == 276
    assert all(entry.indicator_id not in (286, 287, 288) for entry in selection.scoreboard)


def test_tie_is_resolved_by_lowest_indicator_id():
    first = select_profile({275: 0.5, 278: 0.5})
    second = select_profile({278: 0.5, 275: 0.5})
    assert first.profile_id == second.profile_id == 7  # 275 < 278


def test_tie_prefers_buildable_over_lower_id():
    """276 (рекреационная, id меньше) при равенстве уступает застраиваемому 277."""
    selection = select_profile({276: 4.0, 277: 4.0, 278: 4.0, 280: 4.0})
    assert selection.indicator_id == 277  # 276 не застраивается, следующий по id — 277
    assert selection.profile_id == 3
    assert selection.buildable is True


def test_value_beats_buildability_on_no_tie():
    """Застраиваемость — только тай-брейк: не застраиваемый с бóльшим значением побеждает."""
    selection = select_profile({276: 0.9, 278: 0.5})
    assert selection.profile_id == 2
    assert selection.buildable is False


def test_non_buildable_profile_is_flagged():
    selection = select_profile({276: 0.9, 274: 0.1})
    assert selection.profile_id == 2
    assert selection.buildable is False


def test_scoreboard_is_normalized_and_ordered():
    selection = select_profile({271: 0.0, 275: 1.0, 278: 0.5})
    assert [entry.indicator_id for entry in selection.scoreboard] == [275, 278, 271]
    assert selection.scoreboard[0].normalized == 1.0
    assert selection.scoreboard[-1].normalized == 0.0


def test_equal_values_normalize_to_one():
    selection = select_profile({275: 3.0, 278: 3.0})
    assert all(entry.normalized == 1.0 for entry in selection.scoreboard)


def test_missing_indicators_are_reported():
    selection = select_profile({275: 1.0})
    assert 280 in selection.missing_indicator_ids
    assert 275 not in selection.missing_indicator_ids


def test_no_values_raises():
    with pytest.raises(NoIndicatorValuesError):
        select_profile({})


def test_only_foreign_indicators_raises():
    with pytest.raises(NoIndicatorValuesError):
        select_profile({286: 10.0, 999: 1.0})
