"""Gets every enabled source's events for a day: reused when already collected completely, fetched otherwise.

A source is reused when its stored snapshot is `ok` and was taken after the day ended (plus a settle margin for
late processing such as meeting summaries): a past day does not change. Missing, failed, partial or too-early
sources are fetched again, in parallel, redacted and stored. If a fetch fails where earlier data exists, the
earlier data is kept. A failing or missing collector never aborts the run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from daily_log.ports.collectors import Collector
from daily_log.ports.repositories import CollectedEventRepository
from daily_log.services.errors import describe_error
from daily_log.services.redaction import RedactionService
from fde_core.domain.collection import CollectionContext, SourceSnapshot
from fde_core.domain.events import SOURCES, CollectionResult, Source
from fde_core.domain.profile import FdeProfile

log = logging.getLogger(__name__)


@dataclass
class CollectionOutcome:
    results: dict[Source, CollectionResult]
    snapshots: dict[Source, SourceSnapshot] = field(default_factory=dict)
    reused: set[Source] = field(default_factory=set)
    secrets_masked: int = 0


class CollectionService:
    def __init__(
        self,
        collectors_for: Callable[[FdeProfile], list[Collector]],
        store: CollectedEventRepository,
        redaction: RedactionService,
        timeout_s: int,
        settle: timedelta,
        force_recollect: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._collectors_for = collectors_for
        self._store = store
        self._redaction = redaction
        self._timeout_s = timeout_s
        self._settle = settle
        self._force_recollect = force_recollect
        self._clock = clock

    def collect(self, ctx: CollectionContext, reuse_all: bool = False) -> CollectionOutcome:
        """`reuse_all` (review rebuilds) reuses whatever is stored, complete or not; missing sources are fetched."""
        day = ctx.window.date
        stored = self._store.snapshots(ctx.fde_id, day)
        outcome = CollectionOutcome(results={})
        to_fetch: list[Source] = []
        for source in SOURCES:
            snapshot = stored.get(source)
            if not ctx.profile.sources.is_enabled(source):
                outcome.results[source] = CollectionResult(source=source, status="disabled")
            elif (
                snapshot
                and not self._force_recollect
                and (reuse_all or snapshot.is_complete(ctx.window.end_utc, self._settle))
            ):
                self._reuse(ctx, snapshot, outcome)
            else:
                to_fetch.append(source)

        collected_at = self._clock()
        for source, result in self._fetch(ctx, to_fetch).items():
            previous = stored.get(source)
            if result.status == "failed" and previous:
                self._reuse(ctx, previous, outcome, note=f"refresh failed: {result.error}")
                continue
            events, masked = self._redaction.apply(result.events)
            outcome.secrets_masked += masked
            outcome.results[source] = result.model_copy(update={"events": events})
            snapshot = SourceSnapshot(
                fde_id=ctx.fde_id,
                date=day,
                source=source,
                status=result.status,
                collected_at=collected_at,
                event_count=len(events),
                error=result.error,
                expires_at=ctx.expires_at(ctx.window.end_utc),
            )
            self._store.save(snapshot, events)
            outcome.snapshots[source] = snapshot

        for source in SOURCES:
            result = outcome.results[source]
            how = "reused" if source in outcome.reused else "fetched"
            log.info("%s %s: %s, %d events", how, source, result.status, len(result.events))
        outcome.results = {source: outcome.results[source] for source in SOURCES}
        return outcome

    def _reuse(
        self, ctx: CollectionContext, snapshot: SourceSnapshot, outcome: CollectionOutcome, note: str | None = None
    ) -> None:
        events = [
            event.model_copy(update={"ts_local": ctx.window.to_local(event.ts_utc)})  # storage returns UTC
            for event in self._store.load(ctx.fde_id, ctx.window.date, snapshot.source)
        ]
        error = "; ".join(filter(None, [snapshot.error, note])) or None
        outcome.results[snapshot.source] = CollectionResult(
            source=snapshot.source, status=snapshot.status, events=events, error=error
        )
        outcome.snapshots[snapshot.source] = snapshot
        outcome.reused.add(snapshot.source)

    def _fetch(self, ctx: CollectionContext, sources: list[Source]) -> dict[Source, CollectionResult]:
        if not sources:
            return {}
        collectors = {collector.source: collector for collector in self._collectors_for(ctx.profile)}
        results: dict[Source, CollectionResult] = {
            source: CollectionResult(source=source, status="failed", error="no collector available")
            for source in sources
            if source not in collectors
        }
        runnable = [collectors[source] for source in sources if source in collectors]
        if not runnable:
            return results
        pool = ThreadPoolExecutor(max_workers=len(runnable), thread_name_prefix="collector")
        futures = {pool.submit(collector.collect, ctx): collector.source for collector in runnable}
        done, pending = wait(futures, timeout=self._timeout_s)
        for future in done:
            source = futures[future]
            try:
                results[source] = future.result()
            except Exception as error:  # collectors should not raise; keep the run alive if one does
                log.warning("collector %s raised: %s", source, describe_error(error))
                results[source] = CollectionResult(source=source, status="failed", error=describe_error(error))
        for future in pending:
            source = futures[future]
            future.cancel()
            results[source] = CollectionResult(
                source=source, status="failed", error=f"timed out after {self._timeout_s}s"
            )
        pool.shutdown(wait=False, cancel_futures=True)
        return results
