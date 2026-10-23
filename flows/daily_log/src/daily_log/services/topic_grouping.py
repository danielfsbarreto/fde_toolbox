"""Groups the day's events into topics per section, starting from deterministic conversation clusters.

Every section is read for content. Events attributed by a strong rule (customer email domain, a place named
after the customer, the Linear Customer field, the meeting title, the FDE's review) are fixed. Events attributed
only because a message names the customer, and internal or unassigned events, may be filed under another
account by the agent, with a confidence and a reason. Code checks every answer: refs the agent invented
are dropped, refs it left out return as their own cluster's topic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from daily_log.ports.llm import TopicGrouper
from fde_core.domain.events import INTERNAL, MANUAL_MATCH, UNASSIGNED, ActivityEvent
from fde_core.domain.learnings import GROUPING, Guidance
from fde_core.domain.profile import FdeProfile
from fde_core.domain.text import fold, truncate
from fde_core.domain.topics import AccountBrief, EventCluster, EventDigest, GroupingRequest, TopicGroup


@dataclass
class EventRefs:
    """Short refs (e1, e2...) for the day's events, so prompts cite them compactly and code can map them back."""

    ids: dict[str, str] = field(default_factory=dict)  # ref -> event id
    refs: dict[str, str] = field(default_factory=dict)  # event id -> ref
    digests: dict[str, EventDigest] = field(default_factory=dict)  # ref -> digest

    @classmethod
    def build(cls, events: list[ActivityEvent], text_chars: int) -> EventRefs:
        refs = cls()
        for number, event in enumerate(sorted(events, key=lambda event: event.ts_utc), start=1):
            ref = f"e{number}"
            refs.ids[ref] = event.id
            refs.refs[event.id] = ref
            refs.digests[ref] = _digest(ref, event, text_chars)
        return refs


_STRONG = ("email_domain:", "place_name:", "linear_customer", "repo:", "slack_channel:", "teams_channel:", "title:")


def _strong(reason: str) -> bool:
    return reason.startswith(_STRONG) or reason == MANUAL_MATCH


def _is_fixed(event: ActivityEvent) -> bool:
    """Strong rule attributions (and the FDE's corrections) are kept; text mentions and defaults are not."""
    reason = event.match_reason or ""
    return _strong(reason.removeprefix("conversation:"))


def _describe(reason: str | None) -> str:
    reason = reason or ""
    if reason.startswith("conversation:"):
        return f"another message in this conversation ({_describe(reason.removeprefix('conversation:'))})"
    if reason.startswith("alias:"):
        return f"the text names {reason.removeprefix('alias:')}"
    if reason.startswith("ambiguous:"):
        return f"several customers named ({reason.removeprefix('ambiguous:')})"
    if reason.startswith("external:"):
        return f"an outside party ({reason.removeprefix('external:')})"
    if reason.startswith("internal:"):
        return "no customer context"
    return reason


def _merge_same_titles(groups: list[TopicGroup]) -> list[TopicGroup]:
    """Topics with the same title in the same account (e.g. two "Other conversations") become one."""
    merged: dict[tuple[str, str], TopicGroup] = {}
    for group in groups:
        key = (group.account_key, fold(group.title))
        if key in merged:
            first = merged[key]
            merged[key] = first.model_copy(update={"event_refs": [*first.event_refs, *group.event_refs]})
        else:
            merged[key] = group
    return list(merged.values())


def _who(person) -> str:
    return "me" if person.is_self else person.name or person.email or person.handle or "someone"


def _where(event: ActivityEvent) -> str | None:
    location = event.location
    if location.kind == "channel":
        return f"#{location.name or location.id}"
    if location.kind == "repo":
        return location.id
    return location.name


def _digest(ref: str, event: ActivityEvent, text_chars: int) -> EventDigest:
    return EventDigest(
        ref=ref,
        source=event.source,
        kind=event.kind,
        involvement=event.involvement,
        time=event.ts_local.strftime("%H:%M"),
        actor=_who(event.actor),
        with_=[_who(person) for person in event.counterparts][:8],
        where=_where(event),
        title=event.title,
        text=truncate(event.text, text_chars),
    )


class TopicGroupingService:
    def __init__(self, grouper: TopicGrouper, text_chars: int = 600, max_chars: int = 160_000) -> None:
        self._grouper = grouper
        self._text_chars = text_chars
        self._max_chars = max_chars

    def group(
        self, events: list[ActivityEvent], profile: FdeProfile, guidance: Guidance | None = None
    ) -> tuple[list[TopicGroup], EventRefs, int]:
        """Group the whole day in one call: every conversation cluster, tagged with its rule-based account."""
        if not events:
            return [], EventRefs.build([], self._text_chars), 0
        refs = EventRefs.build(events, self._text_limit(events))
        accounts = [
            AccountBrief(
                key=account.key,
                name=account.name,
                description=account.description,
                aliases=account.aliases,
                email_domains=account.email_domains,
            )
            for account in profile.owned_accounts
        ]
        fixed = {refs.refs[event.id] for event in events if _is_fixed(event)}
        rule_account = {refs.refs[event.id]: event.account_key for event in events}
        clusters = self._clusters(events, refs, {refs.ids[ref] for ref in fixed})
        result = self._grouper.group(
            GroupingRequest(
                output_language=profile.output_language,
                accounts=accounts,
                clusters=clusters,
                guidance=(guidance or Guidance()).text(GROUPING),
            )
        )
        valid_keys = {account.key for account in accounts} | {INTERNAL, UNASSIGNED}
        groups = self._validate(result.output.topics, clusters, rule_account, fixed, valid_keys)
        return groups, refs, result.total_tokens

    def _text_limit(self, events: list[ActivityEvent]) -> int:
        """Shrink each event's text so the whole day fits the budget, never below 200 characters."""
        total = sum(min(len(event.text), self._text_chars) for event in events)
        if total <= self._max_chars:
            return self._text_chars
        return max(200, int(self._text_chars * self._max_chars / total))

    @staticmethod
    def _clusters(events: list[ActivityEvent], refs: EventRefs, fixed: set[str]) -> list[EventCluster]:
        by_thread: dict[tuple[str, str], list[ActivityEvent]] = {}
        for event in sorted(events, key=lambda event: event.ts_utc):
            by_thread.setdefault((event.account_key, event.thread_id or event.id), []).append(event)
        return [
            EventCluster(
                cluster=f"c{number}",
                current_account=account,
                attributed_by=_describe(thread[0].match_reason),
                movable=not any(event.id in fixed for event in thread),
                events=[refs.digests[refs.refs[event.id]] for event in thread],
            )
            for number, ((account, _), thread) in enumerate(by_thread.items(), start=1)
        ]

    @staticmethod
    def _validate(
        topics: list[TopicGroup],
        clusters: list[EventCluster],
        rule_account: dict[str, str],
        fixed: set[str],
        valid_keys: set[str],
    ) -> list[TopicGroup]:
        """Unknown refs dropped; each ref used once; fixed refs keep their account (a topic mixing fixed refs of
        several accounts is split); refs left out return as their cluster's topic."""
        seen: set[str] = set()
        groups: list[TopicGroup] = []
        for topic in topics:
            topic_refs = [ref for ref in dict.fromkeys(topic.event_refs) if ref in rule_account and ref not in seen]
            if not topic_refs:
                continue
            seen.update(topic_refs)
            fixed_accounts = list(dict.fromkeys(rule_account[ref] for ref in topic_refs if ref in fixed))
            chosen = topic.account_key if topic.account_key in valid_keys else rule_account[topic_refs[0]]
            if not fixed_accounts:
                parts = {chosen: topic_refs}
            else:
                home = chosen if chosen in fixed_accounts else fixed_accounts[0]
                parts = {account: [] for account in fixed_accounts}
                for ref in topic_refs:
                    parts[rule_account[ref] if ref in fixed else home].append(ref)
            for account, refs_ in parts.items():
                moved = any(rule_account[ref] != account for ref in refs_)
                reason = (topic.reason or f"Grouped with related {account} activity") if moved else ""
                groups.append(
                    topic.model_copy(
                        update={
                            "event_refs": refs_,
                            "account_key": account,
                            "reason": reason,
                            "confidence": topic.confidence if moved else "high",
                        }
                    )
                )
        groups = _merge_same_titles(groups)
        for cluster in clusters:
            missing = [event.ref for event in cluster.events if event.ref not in seen]
            if missing:
                first = cluster.events[0]
                groups.append(
                    TopicGroup(
                        title=first.title or first.where or "Other conversations",
                        workstream="other",
                        account_key=cluster.current_account,
                        event_refs=missing,
                        confidence="high",
                    )
                )
        return groups
