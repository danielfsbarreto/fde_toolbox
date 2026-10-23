"""Everything a collector needs to collect one day for one FDE."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel

from fde_core.domain.events import CoverageStatus, Source
from fde_core.domain.identity import FdeIdentity
from fde_core.domain.profile import FdeProfile
from fde_core.domain.reference import ReferenceData
from fde_core.domain.window import DayWindow


class CollectionContext(BaseModel):
    profile: FdeProfile
    identity: FdeIdentity
    references: ReferenceData
    window: DayWindow
    retention_days: int

    @property
    def fde_id(self) -> str:
        return self.profile.fde_id

    def expires_at(self, ts_utc: datetime) -> datetime:
        return ts_utc + timedelta(days=self.retention_days)


class SourceSnapshot(BaseModel):
    """When and how one source was collected for one day. A complete `ok` snapshot is reused instead of fetching."""

    fde_id: str
    date: date
    source: Source
    status: CoverageStatus
    collected_at: datetime
    event_count: int = 0
    error: str | None = None
    expires_at: datetime

    def is_complete(self, day_end_utc: datetime, settle: timedelta) -> bool:
        """Collected `ok` after the day ended (plus a margin for late processing such as meeting summaries)."""
        return self.status == "ok" and self.collected_at >= day_end_utc + settle
