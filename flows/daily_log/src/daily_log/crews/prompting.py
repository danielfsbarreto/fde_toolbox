"""Compact text views of crew inputs. Events are cited by short refs (e12), never by their long ids."""

from __future__ import annotations

from fde_core.domain.topics import AccountBrief, EventCluster, EventDigest, TopicGroup

LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}


def format_accounts(accounts: list[AccountBrief]) -> str:
    lines = []
    for account in accounts:
        details = [f"domains: {', '.join(account.email_domains)}" if account.email_domains else ""]
        details.append(f"also called: {', '.join(account.aliases)}" if account.aliases else "")
        details.append(f"engagement: {account.description}" if account.description else "")
        lines.append(f"- {account.key}: {account.name} ({'; '.join(part for part in details if part)})")
    return "\n".join(lines) or "(none)"


def _event_line(event: EventDigest) -> str:
    who = event.actor + (f" with {', '.join(event.with_[:5])}" if event.with_ else "")
    head = f"{event.ref} | {event.time} | {event.source} {event.kind} ({event.involvement}) | {who}"
    where = f" | {event.where}" if event.where else ""
    title = f" | {event.title}" if event.title else ""
    text = " ".join(event.text.split())
    return f"{head}{where}{title}\n    {text}" if text else f"{head}{where}{title}"


def format_clusters(clusters: list[EventCluster]) -> str:
    return "\n\n".join(
        f"[{cluster.cluster} | currently: {cluster.current_account}"
        + (f" | attributed by: {cluster.attributed_by}" if cluster.attributed_by else "")
        + ("" if cluster.movable else " | fixed")
        + "]\n"
        + "\n".join(_event_line(e) for e in cluster.events)
        for cluster in clusters
    )


def format_events(events: list[EventDigest]) -> str:
    return "\n".join(_event_line(event) for event in events)


def format_groups(groups: list[TopicGroup]) -> str:
    return "\n".join(
        f"- {group.title} [{group.workstream}]: {', '.join(group.event_refs)}"
        + (f" (attributed by content, {group.confidence}: {group.reason})" if group.reason else "")
        for group in groups
    )
