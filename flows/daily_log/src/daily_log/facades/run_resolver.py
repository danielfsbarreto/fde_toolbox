"""Turns raw kickoff inputs into a RunPlan, and fails fast on bad inputs or a missing profile."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from daily_log.ports.repositories import RunRepository
from daily_log.services.identity import IdentityService
from daily_log.services.profile import ProfileError, ProfileService
from fde_core.domain.run import (
    PREVIOUS_WORKDAY,
    YESTERDAY,
    RunInputError,
    RunPlan,
    RunState,
    previous_working_day,
)
from fde_core.domain.window import today_in


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise RunInputError(f"{field}={value!r} is not a YYYY-MM-DD date") from error


class RunResolver:
    def __init__(
        self,
        profiles: ProfileService,
        identity: IdentityService,
        runs: RunRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._profiles = profiles
        self._identity = identity
        self._runs = runs
        self._clock = clock

    def resolve(self, state: RunState) -> RunPlan:
        fde_id = self._fde_id(state.fde_id.strip())
        profile = self._profiles.load(fde_id)
        today = today_in(profile.timezone, self._clock())
        day = self._date(state.date.strip(), today)
        plan = RunPlan(run_id=state.id, fde_id=fde_id, timezone=profile.timezone, day=day)

        self._runs.start(plan, state.inputs())
        return plan

    def _fde_id(self, requested: str) -> str:
        if requested:
            return requested
        email = self._identity.connected_email()
        fde_id = self._profiles.fde_id_for_email(email)
        if fde_id is None:
            raise ProfileError(
                f"No FDE profile lists the connected account's email ({email}). "
                "Pass fde_id or add the email to a profile."
            )
        return fde_id

    @staticmethod
    def _date(value: str, today: date) -> date:
        """Parse `date`: "previous workday" (or empty), "yesterday", or "YYYY-MM-DD" (one day)."""
        if not value or value.lower() == PREVIOUS_WORKDAY:
            return previous_working_day(today)
        if value.lower() == YESTERDAY:
            return today - timedelta(days=1)
        day = _parse_date(value, "date")
        if day > today:
            raise RunInputError(f"date={value!r} is in the future")
        return day
