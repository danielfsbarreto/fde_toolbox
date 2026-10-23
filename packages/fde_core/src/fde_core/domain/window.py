"""Day windows: one local calendar day expressed as a half-open UTC interval."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class DayWindow(BaseModel):
    date: date
    timezone: str
    start_utc: datetime
    end_utc: datetime

    @classmethod
    def for_day(cls, day: date, tz: str) -> DayWindow:
        zone = ZoneInfo(tz)
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return cls(
            date=day,
            timezone=tz,
            start_utc=start.astimezone(timezone.utc),
            end_utc=end.astimezone(timezone.utc),
        )

    def contains(self, ts: datetime) -> bool:
        return self.start_utc <= ts.astimezone(timezone.utc) < self.end_utc

    def to_local(self, ts: datetime) -> datetime:
        return ts.astimezone(ZoneInfo(self.timezone))


def today_in(tz: str, now: datetime) -> date:
    return now.astimezone(ZoneInfo(tz)).date()
