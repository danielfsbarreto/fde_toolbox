"""Helpers shared by collectors: timestamps, the failure-safe wrapper, partial-error tracking."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from daily_log.ports.integrations import ActionError
from daily_log.services.errors import describe_error
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, Source

log = logging.getLogger(__name__)


class CollectorErrors:
    """Errors from secondary calls: the collector keeps going and reports `partial`."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def record(self, error: BaseException) -> None:
        self.messages.append(describe_error(error, limit=160))

    def summary(self) -> str | None:
        if not self.messages:
            return None
        unique = list(dict.fromkeys(self.messages))
        return f"{len(self.messages)} call(s) failed: " + "; ".join(unique[:3])


def run_collector(
    source: Source,
    ctx: CollectionContext,
    collect: Callable[[CollectionContext, CollectorErrors], list[ActivityEvent]],
) -> CollectionResult:
    errors = CollectorErrors()
    try:
        events = collect(ctx, errors)
    except (ActionError, ValueError, KeyError, TypeError) as error:
        log.warning("%s collector failed: %s", source, describe_error(error))
        return CollectionResult(source=source, status="failed", error=describe_error(error))
    return CollectionResult(
        source=source,
        status="partial" if errors.messages else "ok",
        events=events,
        error=errors.summary(),
    )


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso_z(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_range(ctx: CollectionContext) -> str:
    """Inclusive range for search qualifiers, e.g. updated:2026-10-13T03:00:00Z..2026-10-14T02:59:59Z."""
    return f"{iso_z(ctx.window.start_utc)}..{iso_z(ctx.window.end_utc - timedelta(seconds=1))}"
