"""Writes to a stored day, shared by the flow (agent requests, finalize) and the review UI (direct edits).

Topics are the reviewable ones (not "added by you"): manual topics always come from the day's manual entries.
Events follow their topic's account, so a topic moved to another account takes its events with it, and the
report, the activity and the counts stay consistent.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timezone

from daily_log.ports.repositories import DailyLogRepository, DayOverridesRepository, EventRepository
from daily_log.services.log_rendering import LogRenderingService
from daily_log.services.profile import ProfileService
from daily_log.services.topic_synthesis import WrittenSection
from fde_core.domain.daily_log import AccountDigest, ActivityLine, DailyLog
from fde_core.domain.events import INTERNAL, UNASSIGNED, ActivityEvent
from fde_core.domain.profile import FdeProfile
from fde_core.domain.review import DayOverrides
from fde_core.domain.topics import Topic
from fde_core.domain.window import DayWindow

REVIEW_MATCH = "review"


def reviewable_topics(log: DailyLog) -> list[Topic]:
    """The day's topics in display order, without the manual ones (they are edited as manual entries)."""
    topics = [topic for digest in log.accounts for topic in digest.topics] + log.internal + log.unassigned
    return [topic for topic in topics if topic.attribution != "manual"]


def account_digests(
    profile: FdeProfile, events: list[ActivityEvent], written: dict[str, WrittenSection]
) -> list[AccountDigest]:
    active = Counter(event.account_key for event in events)
    return [
        AccountDigest(
            account_key=account.key,
            account_name=account.name,
            headline=written[account.key].headline if account.key in written else "",
            topics=written[account.key].topics if account.key in written else [],
            quiet=active[account.key] == 0,
        )
        for account in profile.owned_accounts
    ]


def activity(events: list[ActivityEvent]) -> dict[str, list[ActivityLine]]:
    """One line per event, except chat messages, comments and commits: one line per conversation (a thread,
    a DM or channel day, the comments on one issue or PR, the commits on one branch)."""
    conversations: dict[tuple[str, str], list[ActivityEvent]] = {}
    lines: list[tuple[str, datetime, ActivityLine]] = []
    for event in sorted(events, key=lambda event: event.ts_utc):
        if event.kind in ("message", "comment", "commit") and event.thread_id:
            conversations.setdefault((event.account_key, event.thread_id), []).append(event)
        else:
            lines.append((event.account_key, event.ts_utc, ActivityLine.from_event(event)))
    lines += [
        (key, group[0].ts_utc, ActivityLine.from_conversation(group)) for (key, _), group in conversations.items()
    ]
    result: dict[str, list[ActivityLine]] = {}
    for key, _, line in sorted(lines, key=lambda item: item[1]):
        result.setdefault(key, []).append(line)
    return result


def with_manual(
    log: DailyLog, manual_topics: list[Topic], events: list[ActivityEvent], comments: dict[str, str]
) -> DailyLog:
    """What the FDE added in review: manual topics sit under their account, after the written ones (activity
    includes the manual events), and comments sit on their account."""

    def merge(topics: list[Topic], key: str) -> list[Topic]:
        return [topic for topic in topics if topic.attribution != "manual"] + [
            topic for topic in manual_topics if topic.account_key == key
        ]

    active = Counter(event.account_key for event in events)
    accounts = [
        digest.model_copy(
            update={"topics": merge(digest.topics, digest.account_key), "quiet": active[digest.account_key] == 0}
        )
        for digest in log.accounts
    ]
    return log.model_copy(
        update={
            "accounts": accounts,
            "internal": merge(log.internal, INTERNAL),
            "unassigned": merge(log.unassigned, UNASSIGNED),
            "activity": activity(events),
            "comments": dict(comments),
        }
    )


class DayWriter:
    def __init__(
        self,
        *,
        profiles: ProfileService,
        events: EventRepository,
        daily_logs: DailyLogRepository,
        overrides: DayOverridesRepository,
        rendering: LogRenderingService,
        retention_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._profiles = profiles
        self._events = events
        self._daily_logs = daily_logs
        self._overrides = overrides
        self._rendering = rendering
        self._retention_days = retention_days
        self._clock = clock

    def load(self, fde_id: str, day: date) -> DailyLog:
        log = self._daily_logs.get(fde_id, day)
        if log is None:
            raise LookupError(f"no daily log for {fde_id} on {day}")
        return log

    def overrides(self, fde_id: str, day: date) -> DayOverrides:
        return self._overrides.get(fde_id, day) or DayOverrides(fde_id=fde_id, date=day)

    def save_overrides(self, overrides: DayOverrides) -> None:
        self._overrides.save(overrides)

    def replace_topics(
        self,
        log: DailyLog,
        topics: list[Topic],
        *,
        headlines: dict[str, str] | None = None,
        removed: list[Topic] | None = None,
        notes: list[str] | None = None,
        status: str | None = None,
        expected_version: int | None = None,
    ) -> DailyLog:
        """Store the day's reviewable topics. Events follow their topic's account."""
        profile = self._profiles.load(log.fde_id)
        window = DayWindow.for_day(log.date, profile.timezone)
        current = {digest.account_key: digest.headline for digest in log.accounts}
        headlines = current | (headlines or {})
        account_of = {event_id: topic.account_key for topic in topics for event_id in topic.evidence}
        events = []
        for event in self._events.list_day(log.fde_id, log.date):
            event = event.model_copy(update={"ts_local": window.to_local(event.ts_utc)})
            target = account_of.get(event.id)
            if target and target != event.account_key and event.source != "manual":
                rule_account = event.meta.get("rule_account", event.account_key)
                event = event.model_copy(
                    update={
                        "account_key": target,
                        "match_reason": REVIEW_MATCH,
                        "meta": {**event.meta, "rule_account": rule_account},
                    }
                )
            events.append(event)
        self._events.replace_day(log.fde_id, log.date, events)
        keys = {topic.account_key for topic in topics} | set(headlines)
        written = {
            key: WrittenSection(
                headline=headlines.get(key, ""), topics=[topic for topic in topics if topic.account_key == key]
            )
            for key in keys
        }
        updated = log.model_copy(
            update={
                "accounts": account_digests(profile, events, written),
                "internal": written[INTERNAL].topics if INTERNAL in written else [],
                "unassigned": written[UNASSIGNED].topics if UNASSIGNED in written else [],
                "removed": log.removed if removed is None else removed,
                "unapplied_feedback": log.unapplied_feedback if notes is None else notes,
                "review_status": status or log.review_status,
            }
        )
        overrides = self.overrides(log.fde_id, log.date)
        updated = with_manual(
            updated, overrides.manual_topics(window, self._retention_days), events, overrides.comments
        )
        return self._save(updated, profile, expected_version)

    def sync_manual(self, log: DailyLog, *, expected_version: int | None = None, final: bool = False) -> DailyLog:
        """Bring the day's manual entries (as events and topics) and comments into the log; `final` approves it."""
        profile = self._profiles.load(log.fde_id)
        window = DayWindow.for_day(log.date, profile.timezone)
        overrides = self.overrides(log.fde_id, log.date)
        events = [event for event in self._events.list_day(log.fde_id, log.date) if event.source != "manual"]
        events = [event.model_copy(update={"ts_local": window.to_local(event.ts_utc)}) for event in events]
        events += overrides.manual_events(window, self._retention_days)
        self._events.replace_day(log.fde_id, log.date, events)
        updated = with_manual(log, overrides.manual_topics(window, self._retention_days), events, overrides.comments)
        if final:
            updated.review_status = "final"
            updated.reviewed_at = self._clock()
        return self._save(updated, profile, expected_version)

    def save(self, log: DailyLog, *, expected_version: int | None = None) -> DailyLog:
        """Re-render and save a log changed in place (headlines, statuses)."""
        return self._save(log, self._profiles.load(log.fde_id), expected_version)

    def _save(self, log: DailyLog, profile: FdeProfile, expected_version: int | None) -> DailyLog:
        log.markdown = self._rendering.render(log, profile.name)
        self._daily_logs.save(log, expected_version=expected_version)
        return log
