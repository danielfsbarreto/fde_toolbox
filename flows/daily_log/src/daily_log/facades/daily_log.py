"""Daily log use case: collect (or reuse) each source, normalize, merge meetings, match accounts, filter, group
into topics, write them, and save the DailyLog.

A day already approved in review is kept when nothing changed in its sources. Writes made in review (direct
edits, agent requests, finalize) go through services/day_writer.py.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from daily_log.ports.repositories import (
    DailyLogRepository,
    DayOverridesRepository,
    EventRepository,
    RunRepository,
)
from daily_log.services.account_matching import AccountMatchingService
from daily_log.services.collection import CollectionOutcome, CollectionService
from daily_log.services.day_writer import account_digests, activity, with_manual
from daily_log.services.errors import describe_error
from daily_log.services.guidance import GuidanceService
from daily_log.services.identity import IdentityService
from daily_log.services.log_rendering import LogRenderingService
from daily_log.services.meeting_merge import MeetingMergeService
from daily_log.services.normalization import NormalizationService
from daily_log.services.profile import ProfileService
from daily_log.services.reference_data import ReferenceDataService
from daily_log.services.relevance import RelevanceService
from daily_log.services.topic_grouping import EventRefs, TopicGroupingService
from daily_log.services.topic_synthesis import TopicSynthesisService
from fde_core.domain.collection import CollectionContext
from fde_core.domain.daily_log import DailyLog, SourceCoverage
from fde_core.domain.events import INTERNAL, UNASSIGNED, ActivityEvent, CollectionResult, Source
from fde_core.domain.identity import FdeIdentity
from fde_core.domain.profile import FdeProfile
from fde_core.domain.reference import ReferenceData
from fde_core.domain.review import DayOverrides
from fde_core.domain.run import DayResult, RunPlan
from fde_core.domain.topics import TopicGroup
from fde_core.domain.window import DayWindow


@dataclass(frozen=True)
class RunContext:
    """The FDE's profile, identity and reference data, resolved for the run."""

    profile: FdeProfile
    identity: FdeIdentity
    references: ReferenceData


class DailyLogFacade:
    def __init__(
        self,
        *,
        profiles: ProfileService,
        identity: IdentityService,
        references: ReferenceDataService,
        collection: CollectionService,
        normalization: NormalizationService,
        meeting_merge: MeetingMergeService,
        matching: AccountMatchingService,
        relevance: RelevanceService,
        grouping: TopicGroupingService,
        synthesis: TopicSynthesisService,
        rendering: LogRenderingService,
        events: EventRepository,
        daily_logs: DailyLogRepository,
        overrides: DayOverridesRepository,
        runs: RunRepository,
        guidance: GuidanceService,
        retention_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._profiles = profiles
        self._identity = identity
        self._references = references
        self._collection = collection
        self._normalization = normalization
        self._meeting_merge = meeting_merge
        self._matching = matching
        self._relevance = relevance
        self._grouping = grouping
        self._synthesis = synthesis
        self._rendering = rendering
        self._events = events
        self._daily_logs = daily_logs
        self._overrides = overrides
        self._runs = runs
        self._guidance = guidance
        self._retention_days = retention_days
        self._clock = clock

    def run(self, plan: RunPlan) -> DayResult:
        try:
            result = self._run(plan)
        except Exception as error:
            self._runs.fail(plan.run_id, plan.fde_id, describe_error(error))
            raise
        self._runs.record_result(plan.run_id, plan.fde_id, result)
        return result

    def _run(self, plan: RunPlan) -> DayResult:
        day = plan.day
        previous = self._daily_logs.get(plan.fde_id, day)
        if previous is not None and previous.review_status == "final":
            # A final day is the record of what happened: a run never collects or rebuilds it. To redo it, delete it
            # in the review app's Settings and run it again.
            return self._day_result(plan, day, previous, None)
        profile = self._profiles.load(plan.fde_id)
        context = RunContext(
            profile=profile,
            identity=self._identity.resolve(profile),
            references=self._references.resolve(profile),
        )
        ctx = self._context(context, day)
        outcome = self._collection.collect(ctx)
        log = self._build(plan, profile, ctx, outcome)
        return self._day_result(plan, day, log, outcome)

    # Building ------------------------------------------------------------------------------------

    def _context(self, context: RunContext, day: date) -> CollectionContext:
        return CollectionContext(
            profile=context.profile,
            identity=context.identity,
            references=context.references,
            window=DayWindow.for_day(day, context.profile.timezone),
            retention_days=self._retention_days,
        )

    def _build(
        self, plan: RunPlan, profile: FdeProfile, ctx: CollectionContext, outcome: CollectionOutcome
    ) -> DailyLog:
        day = ctx.window.date
        results = outcome.results
        events, dropped = self._normalization.normalize(results)
        events, merged = self._meeting_merge.merge(events)
        events = self._matching.match(events, ctx)
        kept, irrelevant = self._relevance.filter(events)
        overrides = self._overrides.get(plan.fde_id, day) or DayOverrides(fde_id=plan.fde_id, date=day)

        guidance = self._guidance.load(profile)  # the FDE's approved learnings, for both LLM steps
        groups, refs, grouping_tokens = self._grouping.group(kept, profile, guidance)
        kept = self._attribute_by_content(kept, groups, refs)
        written, writing_tokens = self._synthesis.write(groups, refs, profile, guidance)

        window = ctx.window
        events_final = kept + overrides.manual_events(window, self._retention_days)
        self._events.replace_day(plan.fde_id, day, events_final)
        coverage = {
            source: SourceCoverage(
                status=result.status,
                event_count=len(result.events),
                error=result.error,
                reused=source in outcome.reused,
                collected_at=outcome.snapshots[source].collected_at if source in outcome.snapshots else None,
            )
            for source, result in results.items()
        }
        log = DailyLog(
            fde_id=plan.fde_id,
            date=day,
            generated_at=self._clock(),
            run_id=plan.run_id,
            accounts=account_digests(profile, kept, written),
            internal=written[INTERNAL].topics if INTERNAL in written else [],
            unassigned=written[UNASSIGNED].topics if UNASSIGNED in written else [],
            activity=activity(kept),
            coverage=coverage,
            stats={
                "collected_by_source": {source: len(result.events) for source, result in results.items()},
                "dropped_by_reason": dict(dropped + irrelevant),
                "redaction": {"secrets_masked": outcome.secrets_masked},
                "meetings": {"recordings_merged": merged},
                "collection": {"sources_reused": len(outcome.reused)},
                "kept_by_source": dict(Counter(event.source for event in kept)),
                "kept_by_account": dict(Counter(event.account_key for event in kept)),
            },
            warnings=list(ctx.references.warnings),
        )
        log.usage.add_crew("topic_grouping", grouping_tokens)
        log.usage.add_crew("topic_synthesis", writing_tokens)
        manual = overrides.manual_topics(window, self._retention_days)
        log = with_manual(log, manual, events_final, overrides.comments)
        log.markdown = self._rendering.render(log, profile.name)
        self._daily_logs.save(log)
        return log

    def _day_result(self, plan: RunPlan, day: date, log: DailyLog, outcome: CollectionOutcome | None) -> DayResult:
        """outcome is None for a final day, which is kept as is (nothing collected)."""
        return DayResult(
            date=day,
            status=self._day_status(outcome.results) if outcome else "ok",
            event_count=sum(len(line.event_ids) for lines in log.activity.values() for line in lines),
            topic_count=sum(len(digest.topics) for digest in log.accounts) + len(log.internal) + len(log.unassigned),
            coverage={source: result.status for source, result in outcome.results.items()} if outcome else {},
            daily_log_ref=f"daily_logs/{plan.fde_id}/{day.isoformat()}",
        )

    @staticmethod
    def _attribute_by_content(
        events: list[ActivityEvent], groups: list[TopicGroup], refs: EventRefs
    ) -> list[ActivityEvent]:
        """Events follow their topic when the agent filed it under another account (shown as inferred)."""
        moved = {refs.ids[ref]: group for group in groups if group.reason for ref in group.event_refs}
        result = []
        for event in events:
            group = moved.get(event.id)
            if group is None or group.account_key == event.account_key:
                result.append(event)
                continue
            result.append(
                event.model_copy(
                    update={
                        "account_key": group.account_key,
                        "match_reason": f"content:{group.confidence}",
                        "meta": {**event.meta, "rule_account": event.account_key},
                    }
                )
            )
        return result

    @staticmethod
    def _day_status(results: dict[Source, CollectionResult]) -> str:
        statuses = {result.status for result in results.values() if result.status != "disabled"}
        return "ok" if statuses <= {"ok"} else "partial"
