"""Agent requests from the review: a deeper look at what was already fetched, or a rework of a section.

Each request has a scope (the day, an account or a topic). The agent sees only the topics and events in that
scope and returns what it changes: rewritten topics (by id) and missed ones. Code keeps it honest: topics
outside the scope never change, evidence must be events in scope, accounts must be known.
"""

from __future__ import annotations

from daily_log.ports.llm import DeepReviewer, DeepReviewRequest
from daily_log.services.day_writer import reviewable_topics
from daily_log.services.topic_grouping import EventRefs
from fde_core.domain.daily_log import DailyLog
from fde_core.domain.events import INTERNAL, UNASSIGNED, ActivityEvent
from fde_core.domain.learnings import REVIEW, Guidance
from fde_core.domain.profile import FdeProfile
from fde_core.domain.review_request import AgentRequest, ReviewedTopic
from fde_core.domain.topics import FollowUp, Topic

LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}
MIN_TEXT_CHARS = 200
MAX_TEXT_CHARS = 1200
AGENT_REASON = "added in a deeper review"


def _accounts(profile: FdeProfile) -> dict[str, str]:
    accounts = {account.key: account.name for account in profile.owned_accounts}
    return accounts | {INTERNAL: "Internal (CrewAI work with no customer of theirs)", UNASSIGNED: "Unassigned"}


def _format_event(digest) -> str:
    who = digest.actor + (f" with {', '.join(digest.with_[:5])}" if digest.with_ else "")
    head = f"{digest.ref} | {digest.time} | {digest.source} {digest.kind} | {who}"
    head += (f" | {digest.where}" if digest.where else "") + (f" | {digest.title}" if digest.title else "")
    text = " ".join(digest.text.split())
    return f"{head}\n    {text}" if text else head


def _format_topic(topic: Topic, refs: EventRefs) -> str:
    lines = [f"id {topic.id} [{topic.account_key}] {topic.title} ({topic.workstream}, {topic.my_involvement})"]
    lines.append(f"  What happened: {topic.what_happened}")
    if topic.status_or_decision:
        lines.append(f"  Status: {topic.status_or_decision}")
    lines += [f"  Risk: {risk}" for risk in topic.risks]
    for item in topic.follow_ups:
        lines.append(f"  Follow-up: {item.what}" + (f" (owner {item.owner})" if item.owner else ""))
    if topic.people:
        lines.append(f"  People: {', '.join(topic.people)}")
    cited = [refs.refs[event_id] for event_id in topic.evidence if event_id in refs.refs]
    lines.append(f"  Evidence: {', '.join(cited) or 'none in scope'}")
    return "\n".join(lines)


class DeepReviewService:
    def __init__(self, reviewer: DeepReviewer, max_chars: int = 160_000) -> None:
        self._reviewer = reviewer
        self._max_chars = max_chars

    def review(
        self,
        requests: list[AgentRequest],
        log: DailyLog,
        events: list[ActivityEvent],
        profile: FdeProfile,
        guidance: Guidance | None = None,
    ) -> tuple[list[Topic], list[str], int]:
        """The day's reviewable topics after every request, notes from the agent, and the tokens used."""
        topics = reviewable_topics(log)
        notes: list[str] = []
        tokens = 0
        accounts = _accounts(profile)
        names = {key: name for key, name in accounts.items()}
        for request in requests:
            in_scope, scope_events, label = self._scope(request, topics, events, names)
            if label is None:
                notes.append(f"Not found: the {request.scope.kind} of the request ({request.scope.key}).")
                continue
            refs = EventRefs.build(scope_events, self._text_chars(scope_events))
            result = self._reviewer.review(
                DeepReviewRequest(
                    instruction=request.instruction,
                    scope=label,
                    accounts="\n".join(f"- {key}: {name}" for key, name in accounts.items()),
                    topics="\n\n".join(_format_topic(topic, refs) for topic in in_scope) or "(none)",
                    events="\n".join(_format_event(digest) for digest in refs.digests.values()) or "(none)",
                    output_language=LANGUAGES.get(profile.output_language, "English"),
                    # The day's learnings for every step; for an account or a topic, those of its accounts
                    guidance=(guidance or Guidance()).text(
                        REVIEW,
                        None
                        if request.scope.kind == "day"
                        else {topic.account_key for topic in in_scope}
                        | ({request.scope.key} if request.scope.kind == "account" and request.scope.key else set()),
                    ),
                )
            )
            tokens += result.total_tokens
            topics = self._apply(topics, {topic.id for topic in in_scope}, result.output, refs, accounts, notes)
            notes += [note for note in result.output.notes if note.strip()]
        return topics, notes, tokens

    def _scope(
        self, request: AgentRequest, topics: list[Topic], events: list[ActivityEvent], names: dict[str, str]
    ) -> tuple[list[Topic], list[ActivityEvent], str | None]:
        scope = request.scope
        if scope.kind == "day":
            return topics, events, "the whole day"
        if scope.kind == "account":
            if scope.key not in names:
                return [], [], None
            in_scope = [topic for topic in topics if topic.account_key == scope.key]
            cited = {event_id for topic in in_scope for event_id in topic.evidence}
            scoped = [event for event in events if event.account_key == scope.key or event.id in cited]
            return in_scope, scoped, f"the {names[scope.key]} account"
        topic = next((topic for topic in topics if topic.id == scope.key), None)
        if topic is None:
            return [], [], None
        cited = set(topic.evidence)
        scoped = [event for event in events if event.id in cited or event.account_key == topic.account_key]
        return [topic], scoped, f'the topic "{topic.title}"'

    def _text_chars(self, events: list[ActivityEvent]) -> int:
        if not events:
            return MAX_TEXT_CHARS
        return max(MIN_TEXT_CHARS, min(MAX_TEXT_CHARS, self._max_chars // len(events)))

    @staticmethod
    def _apply(
        topics: list[Topic],
        scope_ids: set[str],
        review,
        refs: EventRefs,
        accounts: dict[str, str],
        notes: list[str],
    ) -> list[Topic]:
        def evidence(item: ReviewedTopic) -> list[str]:
            return list(dict.fromkeys(refs.ids[ref.strip()] for ref in item.evidence if ref.strip() in refs.ids))

        def follow_ups(item: ReviewedTopic, previous: list[FollowUp]) -> list[FollowUp]:
            kept = {(follow_up.what, follow_up.owner): follow_up for follow_up in previous}
            return [kept.get((follow_up.what, follow_up.owner), follow_up) for follow_up in item.follow_ups]

        by_id = {topic.id: topic for topic in topics}
        changed: dict[str, Topic] = {}
        for item in review.changed_topics:
            original = by_id.get(item.id or "")
            if original is None or original.id not in scope_ids:
                notes.append(f"Ignored a change to a topic outside the request: {item.title}.")
                continue
            account = item.account_key if item.account_key in accounts else original.account_key
            fields = item.model_dump(exclude={"id", "evidence", "follow_ups", "account_key"})
            changed[original.id] = original.model_copy(
                update={
                    **fields,
                    "account_key": account,
                    "evidence": evidence(item) or original.evidence,
                    "follow_ups": follow_ups(item, original.follow_ups),
                    "reviewed": False,
                    "attribution": "review" if account != original.account_key else original.attribution,
                    "attribution_reason": "corrected in review"
                    if account != original.account_key
                    else original.attribution_reason,
                }
            )
        added = []
        for item in review.new_topics:
            cited = evidence(item)
            if not cited or item.account_key not in accounts:
                notes.append(f"Skipped a new topic without evidence in scope or with an unknown account: {item.title}.")
                continue
            added.append(
                Topic(
                    **item.model_dump(exclude={"id", "evidence"}),
                    evidence=cited,
                    agent_added=True,
                    attribution="review",
                    attribution_reason=AGENT_REASON,
                )
            )
        return [changed.get(topic.id, topic) for topic in topics] + added
