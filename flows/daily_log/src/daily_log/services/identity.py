"""Discovers who the FDE is in each source by asking their own AMP connections, cached per FDE."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_log.ports.integrations import ActionError, ActionRunner
from daily_log.ports.repositories import ReferenceCacheRepository
from fde_core.domain.identity import FdeIdentity
from fde_core.domain.profile import FdeProfile

log = logging.getLogger(__name__)

_KIND = "identity"


def _data(output: dict[str, Any]) -> dict[str, Any]:
    data = output.get("data", output)
    return data if isinstance(data, dict) else {}


class IdentityService:
    def __init__(
        self,
        actions: ActionRunner,
        cache: ReferenceCacheRepository,
        cache_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._actions = actions
        self._cache = cache
        self._max_age = timedelta(days=cache_days)
        self._clock = clock

    def connected_email(self) -> str:
        """The email of the Google account behind the AMP connection; used to find the FDE's profile."""
        email = _data(self._actions.run("gmail", "gmail_who_am_i")).get("email")
        if not email:
            raise ActionError("gmail", "gmail_who_am_i", "no_email", "connected account has no email")
        return email.lower()

    def resolve(self, profile: FdeProfile) -> FdeIdentity:
        cached = self._cache.get(profile.fde_id, _KIND)
        if cached and self._clock() - cached[1] < self._max_age:
            return FdeIdentity.model_validate(cached[0])
        identity = self._discover(profile)
        self._cache.put(profile.fde_id, _KIND, identity.model_dump())
        return identity

    def _discover(self, profile: FdeProfile) -> FdeIdentity:
        identity = FdeIdentity(emails=list(profile.emails))
        lookups: list[tuple[str, str, str, Callable[[dict[str, Any]], None]]] = [
            ("gmail", "gmail", "gmail_who_am_i", lambda out: self._add_email(identity, _data(out).get("email"))),
            ("slack", "slack", "slack_who_am_i", lambda out: self._slack(identity, _data(out))),
            ("teams", "microsoft_teams", "microsoft_teams_who_am_i", lambda out: self._teams(identity, _data(out))),
            ("github", "github", "get_the_authenticated_user", lambda out: self._github(identity, out)),
            ("linear", "linear", "linear_who_am_i", lambda out: self._linear(identity, _data(out))),
        ]
        for source, app, action, apply in lookups:
            if not profile.sources.is_enabled(source):  # type: ignore[arg-type]
                continue
            try:
                apply(self._actions.run(app, action))
            except ActionError as error:
                log.warning("identity lookup failed: %s", error)
        return identity

    @staticmethod
    def _add_email(identity: FdeIdentity, email: str | None) -> None:
        if email and email.lower() not in identity.emails:
            identity.emails.append(email.lower())

    def _slack(self, identity: FdeIdentity, data: dict[str, Any]) -> None:
        identity.slack_user_id = data.get("user_id")

    def _teams(self, identity: FdeIdentity, data: dict[str, Any]) -> None:
        identity.teams_user_id = data.get("id")
        self._add_email(identity, data.get("mail"))
        self._add_email(identity, data.get("user_principal_name"))

    @staticmethod
    def _github(identity: FdeIdentity, output: dict[str, Any]) -> None:
        identity.github_login = output.get("login") or _data(output).get("login")

    def _linear(self, identity: FdeIdentity, data: dict[str, Any]) -> None:
        self._add_email(identity, data.get("email"))
