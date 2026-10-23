"""Run inputs, plan and result: one run covers one day. Flow state holds these: ids, the date and counts, never
message content."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from fde_core.domain.action_points import SyncSummary
from fde_core.domain.events import CoverageStatus, Source

DEFAULT_FDE_ID = "daniel-barreto"
YESTERDAY = "yesterday"
PREVIOUS_WORKDAY = "previous workday"  # the default: on Monday, Friday; weekends are only run on request
DayStatus = Literal["ok", "partial", "failed"]


class RunInputError(ValueError):
    """A kickoff input is missing or malformed."""


class RunPlan(BaseModel):
    run_id: str
    fde_id: str
    timezone: str
    day: date


class DayResult(BaseModel):
    """The run's result: how its day went."""

    date: date
    status: DayStatus
    event_count: int = 0
    topic_count: int = 0
    coverage: dict[Source, CoverageStatus] = {}
    daily_log_ref: str | None = None
    error: str | None = None


def previous_working_day(day: date) -> date:
    previous = day - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


class RunState(BaseModel):
    """Kickoff inputs (all optional, so a scheduler can send {}):

    - fde_id: the FDE profile (flows/daily_log/src/daily_log/profiles/<fde_id>.yaml)
    - date: "previous workday" (default), "yesterday", or one day (see the field)

    The other fields are filled by the run itself.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    fde_id: str = DEFAULT_FDE_ID
    # "previous workday" (in the FDE's timezone; Monday -> Friday) | "yesterday" | "2026-10-13" (one day)
    date: str = PREVIOUS_WORKDAY
    run: RunPlan | None = None
    result: DayResult | None = None
    action_points: SyncSummary | None = None

    def inputs(self) -> dict[str, str]:
        return {"fde_id": self.fde_id, "date": self.date}
