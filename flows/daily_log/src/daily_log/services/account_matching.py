"""Deterministic account matching. Unmatched events stay "unassigned"; ties stay unassigned too.

Precedence, first level with a match wins:
  1 Linear Customer (the only rule for Linear issues)
  2 explicit overrides: GitHub repo, Slack/Teams channel listed in the profile
  3 counterpart email domain (senders, recipients, attendees, chat members)
  4 place name: channel, chat, team or repo whose name contains the key, name or an alias
  5 name / aliases in the title (meetings are matched earlier, by attendee email domains)
  6 name / aliases in the text
Then each conversation (events sharing a thread_id: a thread, a DM or channel day, an email thread, a PR) takes
the account its events matched: one account decides for all of them; two different accounts make the whole
conversation unassigned and ambiguous. Linear issues keep their own Customer-based result.
Then: listed internal channels / keywords -> internal (kept always). Otherwise an event naming no customer is
internal when only CrewAI people are involved, and unassigned when an outside party is (agent may suggest later).
"""

from __future__ import annotations

from collections.abc import Callable

from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import DEFAULT_INTERNAL, EXPLICIT_INTERNAL, INTERNAL, UNASSIGNED, ActivityEvent
from fde_core.domain.text import contains_term

Rule = Callable[[ActivityEvent, CollectionContext], dict[str, str]]


def _linear(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    customers = set(event.meta.get("linear_customer_ids", []))  # type: ignore[arg-type]
    return {
        key: "linear_customer"
        for key, refs in ctx.references.accounts.items()
        if customers & set(refs.linear_customer_ids)
    }


def _repo(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    if event.location.kind != "repo":
        return {}
    repo = event.location.id.lower()
    return {account.key: f"repo:{repo}" for account in ctx.profile.owned_accounts if repo in account.github_repos}


def _channel(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    channel = event.location.id
    matches = {
        key: f"slack_channel:{channel}"
        for key, refs in ctx.references.accounts.items()
        if channel in refs.slack_channel_ids
    }
    matches.update(
        {
            key: f"teams_channel:{channel}"
            for key, refs in ctx.references.accounts.items()
            if channel in refs.teams_channels
        }
    )
    return matches


def _domain(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    domains = {person.domain for person in [event.actor, *event.counterparts] if person.domain}
    domains -= set(ctx.profile.internal.our_domains())
    return {
        account.key: f"email_domain:{domain}"
        for account in ctx.profile.owned_accounts
        for domain in domains
        if any(domain == own or domain.endswith("." + own) for own in account.email_domains)
    }


def _place_name(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    if event.location.kind not in ("channel", "chat", "group_dm", "repo"):
        return {}
    place = " ".join(filter(None, [event.location.name, event.location.id]))
    matches = {account.key: account.names_place(place) for account in ctx.profile.owned_accounts}
    return {key: f"place_name:{term}" for key, term in matches.items() if term}


def _title(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    title = event.title or ""
    return {
        account.key: f"title:{term}"
        for account in ctx.profile.owned_accounts
        for term in [account.name, *account.aliases]
        if title and contains_term(title, term)
    }


def _text(event: ActivityEvent, ctx: CollectionContext) -> dict[str, str]:
    if event.kind == "meeting":  # meetings go by attendee domains and title; summaries name many customers
        return {}
    return {
        account.key: f"alias:{term}"
        for account in ctx.profile.owned_accounts
        for term in [account.name, *account.aliases]
        if contains_term(event.text, term)
    }


_RULES: tuple[Rule, ...] = (_linear, _repo, _channel, _domain, _place_name, _title, _text)


class AccountMatchingService:
    def match(self, events: list[ActivityEvent], ctx: CollectionContext) -> list[ActivityEvent]:
        return self._by_conversation([self._match_one(event, ctx) for event in events], ctx)

    @staticmethod
    def _by_conversation(events: list[ActivityEvent], ctx: CollectionContext) -> list[ActivityEvent]:
        owned = {account.key for account in ctx.profile.owned_accounts}
        accounts: dict[str, set[str]] = {}
        reasons: dict[str, str] = {}
        for event in events:
            if event.thread_id and event.source != "linear" and event.account_key in owned:
                accounts.setdefault(event.thread_id, set()).add(event.account_key)
                reasons.setdefault(event.thread_id, event.match_reason or "")
        result = []
        for event in events:
            keys = accounts.get(event.thread_id or "", set())
            if event.source == "linear" or not keys or keys == {event.account_key}:
                result.append(event)
            elif len(keys) == 1:
                key = next(iter(keys))
                reason = f"conversation:{reasons[event.thread_id]}"  # type: ignore[index]
                result.append(event.model_copy(update={"account_key": key, "match_reason": reason}))
            else:
                reason = "ambiguous:" + ",".join(sorted(keys))
                result.append(event.model_copy(update={"account_key": UNASSIGNED, "match_reason": reason}))
        return result

    def _match_one(self, event: ActivityEvent, ctx: CollectionContext) -> ActivityEvent:
        rules = (_linear,) if event.source == "linear" else _RULES
        for rule in rules:
            matches = rule(event, ctx)
            if len(matches) == 1:
                key, reason = next(iter(matches.items()))
                return event.model_copy(update={"account_key": key, "match_reason": reason})
            if len(matches) > 1:
                reason = "ambiguous:" + ",".join(sorted(matches))
                return event.model_copy(update={"account_key": UNASSIGNED, "match_reason": reason})
        if self._is_listed_internal(event, ctx):
            return event.model_copy(update={"account_key": INTERNAL, "match_reason": EXPLICIT_INTERNAL})
        outsider = self._outsider(event, ctx)
        if outsider:
            return event.model_copy(update={"account_key": UNASSIGNED, "match_reason": f"external:{outsider}"})
        return event.model_copy(update={"account_key": INTERNAL, "match_reason": DEFAULT_INTERNAL})

    @staticmethod
    def _is_listed_internal(event: ActivityEvent, ctx: CollectionContext) -> bool:
        if event.location.id in ctx.references.internal_slack_channel_ids:
            return True
        haystack = f"{event.title or ''}\n{event.text}\n{event.location.name or ''}"
        return any(contains_term(haystack, keyword) for keyword in ctx.profile.internal.keywords)

    @staticmethod
    def _outsider(event: ActivityEvent, ctx: CollectionContext) -> str | None:
        """An external party with no owned account behind it: a domain outside CrewAI, or a repo outside its orgs."""
        internal_domains = ctx.profile.internal.our_domains()
        for person in [event.actor, *event.counterparts]:
            domain = person.domain
            if domain and not any(domain == own or domain.endswith("." + own) for own in internal_domains):
                return domain
        if event.location.kind == "repo":
            owner = event.location.id.split("/", 1)[0].lower()
            if owner not in {org.lower() for org in ctx.profile.sources.github.orgs}:
                return f"repo:{event.location.id}"
        return None
