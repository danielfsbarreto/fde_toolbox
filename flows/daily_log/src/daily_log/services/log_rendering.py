"""Builds the DailyLog Markdown: Accounts, Internal, Unassigned, Coverage and gaps."""

from __future__ import annotations

from collections import Counter

from fde_core.domain.daily_log import AccountDigest, ActivityLine, DailyLog
from fde_core.domain.events import INTERNAL, UNASSIGNED
from fde_core.domain.topics import Topic

_STATUS = {
    "draft": "draft, waiting for review",
    "working": "the agent is working on your requests",
    "final": "final",
}


def _topic(topic: Topic, links: dict[str, str | None]) -> list[str]:
    title = f"**{topic.title}** ({topic.workstream}, {topic.my_involvement})"
    if topic.attribution == "manual":
        title += " _added by you_"
    if topic.attribution == "content":
        title += f" _account inferred from content ({topic.attribution_confidence}): {topic.attribution_reason}_"
    if topic.inferred:
        title += " _contains inferences_"
    if topic.flagged:
        title += f" _flagged: {topic.flag_reason}_"
    lines = [f"- {title}", f"  {topic.what_happened}"]
    if topic.status_or_decision:
        lines.append(f"  Status: {topic.status_or_decision}")
    lines += [f"  Risk: {risk}" for risk in topic.risks]
    if topic.people:
        lines.append(f"  People: {', '.join(topic.people)}")
    urls = list(dict.fromkeys(url for event_id in topic.evidence if (url := links.get(event_id))))
    cited = (
        " ".join(f"[{number}]({url})" for number, url in enumerate(urls, start=1)) or f"{len(topic.evidence)} events"
    )
    lines.append(f"  Evidence: {cited} (confidence {topic.confidence})")
    return lines


def _follow_ups(topics: list[Topic]) -> list[str]:
    lines = []
    for topic in topics:
        for follow_up in topic.follow_ups:
            details = ", ".join(
                part
                for part in (
                    f"owner {follow_up.owner}" if follow_up.owner else "",
                    f"due {follow_up.due_if_stated}" if follow_up.due_if_stated else "",
                )
                if part
            )
            lines.append(f"- [ ] {follow_up.what}" + (f" ({details})" if details else ""))
    return lines


def _counts(lines: list[ActivityLine]) -> list[str]:
    """One line per section: how many events it holds, by source (the events themselves are in storage)."""
    by_source = Counter(line.source for line in lines for _ in line.event_ids)
    if not by_source:
        return []
    sources = ", ".join(f"{source} {count}" for source, count in by_source.most_common())
    total = by_source.total()
    return [f"_{total} event{'' if total == 1 else 's'}: {sources}_", ""]


def _topics(topics: list[Topic], links: dict[str, str | None]) -> list[str]:
    lines: list[str] = []
    for topic in topics:
        lines += _topic(topic, links)
    follow_ups = _follow_ups(topics)
    if follow_ups:
        lines += ["", "Follow-ups:", *follow_ups]
    return [*lines, ""] if lines else []


def _comment(comment: str | None) -> list[str]:
    return [f"**FDE comment:** {comment}", ""] if comment else []


def _account(
    digest: AccountDigest, activity: list[ActivityLine], links: dict[str, str | None], comment: str | None
) -> list[str]:
    lines = [f"### {digest.account_name}", "", *_comment(comment)]
    if digest.quiet and not digest.topics:
        return lines + ["No activity.", ""]
    if digest.headline:
        lines += [digest.headline, ""]
    return lines + _topics(digest.topics, links) + _counts(activity)


def _section(
    title: str,
    topics: list[Topic],
    activity: list[ActivityLine],
    links: dict[str, str | None],
    comment: str | None,
) -> list[str]:
    lines = [f"## {title}", "", *_comment(comment)]
    if not topics and not activity:
        return lines + ["Nothing to report.", ""]
    return lines + _topics(topics, links) + _counts(activity)


class LogRenderingService:
    def render(self, log: DailyLog, fde_name: str) -> str:
        review = _STATUS[log.review_status]
        lines = [f"# Daily log: {fde_name}, {log.date.isoformat()}", "", f"Review: {review}", ""]
        if log.unapplied_feedback:
            lines += ["Notes from the agent:", "", *[f"- {item}" for item in log.unapplied_feedback], ""]
        if log.removed and log.review_status != "final":
            names = {digest.account_key: digest.account_name for digest in log.accounts}
            names |= {INTERNAL: "Internal", UNASSIGNED: "Unassigned"}
            lines += ["Removed in review:", ""]
            lines += [f"- {topic.title} ({names.get(topic.account_key, topic.account_key)})" for topic in log.removed]
            lines.append("")
        links = {
            event_id: line.url for lines_ in log.activity.values() for line in lines_ for event_id in line.event_ids
        }
        lines += ["## Accounts", ""]
        for digest in log.accounts:
            key = digest.account_key
            lines += _account(digest, log.activity.get(key, []), links, log.comments.get(key))
        for title, key, topics in (("Internal", INTERNAL, log.internal), ("Unassigned", UNASSIGNED, log.unassigned)):
            lines += _section(title, topics, log.activity.get(key, []), links, log.comments.get(key))
        lines += ["## Coverage and gaps", "", "| Source | Status | Events | Error |", "|---|---|---|---|"]
        for source, coverage in log.coverage.items():
            lines.append(f"| {source} | {coverage.status} | {coverage.event_count} | {coverage.error or ''} |")
        if log.warnings:
            lines += ["", *[f"- {warning}" for warning in log.warnings]]
        return "\n".join(lines).rstrip() + "\n"

    def render_review_message(self, log: DailyLog, fde_name: str, round_number: int, link: str | None) -> str:
        """What the review pause shows in AMP (dashboard and email): a short summary and the way to answer.

        The day itself is reviewed item by item in the review UI; this message only says what is there.
        """
        topics = [topic for digest in log.accounts for topic in digest.topics] + log.internal + log.unassigned
        follow_ups = sum(len(topic.follow_ups) for topic in topics)
        lines = [
            f"# Daily log: {fde_name}, {log.date.isoformat()} (review round {round_number})",
            "",
            f"{len(topics)} topics, {follow_ups} follow-ups.",
            "",
        ]
        if link:
            lines += [f"Review it item by item in the app: {link}", ""]
        if log.unapplied_feedback:
            lines += ["Notes from the agent:", "", *[f"- {item}" for item in log.unapplied_feedback], ""]
        for digest in log.accounts:
            if digest.topics:
                lines.append(f"- **{digest.account_name}** ({len(digest.topics)}): {digest.headline}".rstrip(": "))
        if log.internal:
            lines.append(f"- **Internal** ({len(log.internal)})")
        if log.unassigned:
            lines.append(f"- **Unassigned** ({len(log.unassigned)})")
        lines += [
            "",
            "Reply with nothing to finalize the day. Otherwise describe what the agent should look for or rework "
            "(a missed angle, something you remember, an account to redo); you will see the day again.",
        ]
        return "\n".join(lines) + "\n"
