"""E3.9 cron expressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forge.usecases.cron import Cron, CronError


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_every_five_minutes() -> None:
    assert Cron("*/5 * * * *").next_after(at(2026, 10, 23, 10, 2)) == at(2026, 10, 23, 10, 5)
    assert Cron("*/5 * * * *").next_after(at(2026, 10, 23, 10, 5)) == at(2026, 10, 23, 10, 10)


def test_weekdays_at_eight() -> None:
    # 2026-10-23 is a Friday: next 08:00 on Monday-Friday is Monday the 26th
    assert Cron("0 8 * * 1-5").next_after(at(2026, 10, 23, 9, 0)) == at(2026, 10, 26, 8, 0)


def test_sunday_is_zero_or_seven() -> None:
    assert Cron("0 0 * * 0").next_after(at(2026, 10, 23)) == Cron("0 0 * * 7").next_after(
        at(2026, 10, 23)
    )


def test_month_rollover_and_leap_day() -> None:
    assert Cron("30 2 1 1 *").next_after(at(2026, 10, 23)) == at(2027, 1, 1, 2, 30)
    assert Cron("0 0 29 2 *").next_after(at(2026, 10, 23)) == at(2028, 2, 29)


def test_either_day_field_when_both_restricted() -> None:
    cron = Cron("0 0 13 * 5")  # the 13th, or any Friday
    assert cron.next_after(at(2026, 10, 23, 1)) == at(2026, 10, 30)


def test_time_zone() -> None:
    # 08:00 in Rome is 06:00 UTC while summer time lasts, 07:00 UTC after 2026-10-25
    assert Cron("0 8 * * *").next_after(at(2026, 10, 23, 7), "Europe/Rome") == at(2026, 10, 24, 6)
    assert Cron("0 8 * * *").next_after(at(2026, 10, 25, 7), "Europe/Rome") == at(2026, 10, 26, 7)


@pytest.mark.parametrize(
    "bad", ["", "* * * *", "60 * * * *", "* 24 * * *", "*/0 * * * *", "a b c d e"]
)
def test_rejects_bad_expressions(bad: str) -> None:
    with pytest.raises(CronError):
        Cron(bad)


def test_never_matching_expression_is_reported() -> None:
    with pytest.raises(CronError):
        Cron("0 0 31 2 *").next_after(at(2026, 1, 1))
