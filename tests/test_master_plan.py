"""Справка по прогону: сводим ответы SIRTEP к тому, что читает человек."""

from app.pipeline.master_plan import (
    PRIORITY_BRANCH,
    PROVISION_BRANCH,
    build_summary,
    provision_digest,
    schedule_digest,
    summary_text,
)

SCHEDULE = {
    "provision": {
        "house_construction_period": {"1": 1, "2": 2},
        "service_construction_period": {"10": 2},
        "houses_per_period": [1, 1, 0, None],
        "services_per_period": [0, 1, 0, None],
        "houses_area_per_period": [5000, 4000, 0, None],
        "services_area_per_period": [0, 800, 0, None],
        "provided_per_period": [0.4, 0.86, None, None],
        "periods": [1, 2, 3, 4],
        "buildings_comment": "Дома построены полностью",
        "services_comment": None,
    },
    "simple": None,
}

PROVISION = {
    "periods": [1, 2],
    "provision": [{"школа": 0.4}, {"школа": 0.9, "поликлиника": 0.7}],
    "unbuilt_services": ["пожарное депо"],
}


def test_schedule_digest_counts_the_whole_queue():
    digest = schedule_digest(SCHEDULE)
    assert digest["branch"] == PROVISION_BRANCH
    assert digest["periods_planned"] == 4
    assert digest["houses_total"] == 2
    assert digest["services_total"] == 1
    assert digest["houses_area_m2"] == 9000


def test_periods_used_is_the_last_period_with_construction():
    """Пустые хвостовые периоды в срок стройки не входят."""
    assert schedule_digest(SCHEDULE)["periods_used"] == 2


def test_provided_final_ignores_empty_tail():
    """Обеспеченность после конца стройки не считается — берём последнее известное значение."""
    assert schedule_digest(SCHEDULE)["provided_final"] == 0.86


def test_comments_keep_only_what_sirtep_said():
    assert schedule_digest(SCHEDULE)["comments"] == ["Дома построены полностью"]


def test_priority_branch_is_reported_not_invented():
    """Ветка `simple` приходит для профилей 3–7 и 9; цифр обеспеченности в ней нет."""
    digest = schedule_digest({"provision": None, "simple": {"id": [1], "period": [1]}})
    assert digest["branch"] == PRIORITY_BRANCH
    assert digest["by_period"] == []


def test_provision_digest_takes_the_final_period():
    digest = provision_digest(PROVISION)
    assert digest["final_by_service"] == {"поликлиника": 0.7, "школа": 0.9}
    assert digest["unbuilt_services"] == ["пожарное депо"]


def test_summary_keeps_missing_parts_as_none():
    summary = build_summary(buildings=None, published=None, schedule=None, provision=None)
    assert summary["schedule"] is None and summary["provision"] is None
    assert summary["buildings"] == {}


def test_summary_text_reports_buildings_and_queue():
    summary = build_summary(
        buildings={"buildings": 12, "residents": 3400, "living_area_m2": 61000, "services": [{"name": "школа"}]},
        published={"scenario_id": 777},
        schedule=SCHEDULE,
        provision=PROVISION,
    )
    text = summary_text(summary)
    assert "Зданий" in text and "3 400" in text  # разряды разделяются тонким пробелом
    assert "за 2 из 4 периодов" in text
    assert "пожарное депо" in text


def test_summary_text_is_empty_when_there_is_nothing_to_say():
    assert summary_text(build_summary(buildings=None, published=None, schedule=None, provision=None)) == ""
