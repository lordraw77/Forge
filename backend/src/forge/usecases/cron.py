"""Five-field cron expressions (minute hour day-of-month month day-of-week) for use case
schedules (E3.9). Supports `*`, lists, ranges and steps; Sunday is 0 or 7. As in classic cron,
when both day fields are restricted a day matches if either one does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SEARCH_STEPS = 5_000_000
_SEARCH_YEARS = 9  # covers a 29 February after a century year


class CronError(ValueError):
    pass


def _field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        try:
            step = int(step_text) if step_text else 1
            if base == "*":
                start, end = low, high
            elif "-" in base:
                a, _, b = base.partition("-")
                start, end = int(a), int(b)
            else:
                start = int(base)
                end = high if step_text else start
        except ValueError as exc:
            raise CronError(f"invalid cron field {text!r}") from exc
        if step < 1 or start < low or end > high or start > end:
            raise CronError(f"cron value out of range in {text!r}")
        values.update(range(start, end + 1, step))
    return values


class Cron:
    def __init__(self, expression: str) -> None:
        parts = expression.split()
        if len(parts) != 5:
            raise CronError("a cron expression has five fields: minute hour day month weekday")
        sets = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True)]
        self.minutes, self.hours, self.days, self.months, weekdays = sets
        self.weekdays = {d % 7 for d in weekdays}
        self._any_day = parts[2] == "*"
        self._any_weekday = parts[4] == "*"

    def _day_ok(self, moment: datetime) -> bool:
        dom = moment.day in self.days
        dow = (moment.weekday() + 1) % 7 in self.weekdays
        if self._any_day or self._any_weekday:
            return dom and dow
        return dom or dow

    def next_after(self, after: datetime, tz: str = "UTC") -> datetime:
        """First matching minute strictly after `after`, as a UTC datetime."""
        zone = ZoneInfo(tz)
        moment = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = moment.year + _SEARCH_YEARS
        for _ in range(_SEARCH_STEPS):
            if moment.year > limit:
                break
            if moment.month not in self.months:
                moment = (moment.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(
                    day=1
                )
            elif not self._day_ok(moment):
                moment = moment.replace(hour=0, minute=0) + timedelta(days=1)
            elif moment.hour not in self.hours:
                moment = moment.replace(minute=0) + timedelta(hours=1)
            elif moment.minute not in self.minutes:
                moment += timedelta(minutes=1)
            else:
                return moment.astimezone(UTC)
        raise CronError("the expression never matches")
