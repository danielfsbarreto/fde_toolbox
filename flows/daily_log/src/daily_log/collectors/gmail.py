"""Gmail: what the FDE sent, received mail involving an owned account, and replies in threads they wrote in.

Newsletters, promotions, calendar invitations and RSVPs are dropped here. Tool notifications (GitHub, Slack,
Linear, Fathom, Granola) are tagged with `notification_of` so normalization can drop them when that source's
own events were collected.
"""

from __future__ import annotations

from email.utils import getaddresses
from typing import Any

from daily_log.collectors.common import CollectorErrors, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, Attachment, CollectionResult, Involvement, Location, Person
from fde_core.domain.text import email_domain, strip_quoted_reply

APP = "gmail"
_PAGE = 100
_MAX_PAGES = 5
_NOISE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM", "TRASH"}
_TOOL_DOMAINS = {
    "github.com": "github",
    "slack.com": "slack",
    "linear.app": "linear",
    "fathom.video": "fathom",
    "fathom.ai": "fathom",
    "granola.ai": "granola",
    "granola.so": "granola",
}


def _headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload") or {}
    return {header["name"].lower(): header.get("value", "") for header in payload.get("headers", [])}


def _has_calendar_part(part: dict[str, Any]) -> bool:
    if part.get("mimeType") == "text/calendar":
        return True
    return any(_has_calendar_part(child) for child in part.get("parts", []) or [])


def _people(value: str) -> list[Person]:
    return [
        Person(name=name or None, email=address.lower(), domain=email_domain(address))
        for name, address in getaddresses([value])
        if address
    ]


def _matches(domain: str | None, domains: set[str]) -> bool:
    return bool(domain) and any(domain == own or domain.endswith("." + own) for own in domains)


class GmailCollector:
    source = "gmail"

    def __init__(self, actions: ActionRunner) -> None:
        self._actions = actions

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("gmail", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        after, before = int(ctx.window.start_utc.timestamp()), int(ctx.window.end_utc.timestamp())
        sent = self._search(f"in:sent after:{after} before:{before}", errors, required=True)
        received = self._search(f"-in:sent after:{after} before:{before}", errors, required=True)
        my_threads = {message["threadId"] for message in sent}
        account_domains = {domain for account in ctx.profile.owned_accounts for domain in account.email_domains}

        events = []
        for message in sent:
            event = self._event(ctx, message, involvement="authored")
            if event and not self._is_noise(message, account_domains, in_my_thread=True):
                events.append(event)
        for message in received:
            in_my_thread = message["threadId"] in my_threads
            if self._is_noise(message, account_domains, in_my_thread):
                continue
            event = self._event(ctx, message, involvement=None)
            if event is None:
                continue
            people = [event.actor, *event.counterparts]
            involves_account = any(_matches(person.domain, account_domains) for person in people)
            if in_my_thread or involves_account or event.meta.get("notification_of"):
                events.append(event)
        return events

    def _search(self, query: str, errors: CollectorErrors, required: bool) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        page_token = None
        for _ in range(_MAX_PAGES):
            arguments: dict[str, Any] = {"query": query, "max_results": _PAGE, "include_payload": True}
            if page_token:
                arguments["page_token"] = page_token
            try:
                output = self._actions.run(APP, "search-emails", arguments)
            except ActionError as error:
                if required and not messages:
                    raise
                errors.record(error)
                break
            messages += output.get("messages", [])
            page_token = output.get("nextPageToken")
            if not page_token:
                break
        return messages

    @staticmethod
    def _is_noise(message: dict[str, Any], account_domains: set[str], in_my_thread: bool) -> bool:
        if _NOISE_LABELS & set(message.get("labelIds", [])):
            return True
        headers = _headers(message)
        if _has_calendar_part(message.get("payload") or {}) or "calendar-notification@google.com" in headers.get(
            "from", ""
        ):
            return True
        bulk = "list-unsubscribe" in headers or headers.get("precedence", "").lower() in ("bulk", "list", "junk")
        sender = _people(headers.get("from", message.get("sender", "")))
        from_account = bool(sender) and _matches(sender[0].domain, account_domains)
        return bulk and not (from_account or in_my_thread)

    def _event(
        self, ctx: CollectionContext, message: dict[str, Any], involvement: Involvement | None
    ) -> ActivityEvent | None:
        ts = parse_ts(message.get("messageTimestamp"))
        if ts is None or not ctx.window.contains(ts):
            return None
        headers = _headers(message)
        sender = _people(headers.get("from", message.get("sender", "")))
        recipients = _people(", ".join(filter(None, [headers.get("to", message.get("to", "")), headers.get("cc", "")])))
        actor = sender[0] if sender else Person()
        actor.is_self = ctx.identity.is_self_email(actor.email)
        for person in recipients:
            person.is_self = ctx.identity.is_self_email(person.email)
        if involvement is None:
            involvement = (
                "authored" if actor.is_self else "participated" if any(p.is_self for p in recipients) else "observed"
            )
        notification_of = next((tool for domain, tool in _TOOL_DOMAINS.items() if _matches(actor.domain, {domain})), "")
        labels = message.get("labelIds", [])
        return ActivityEvent(
            id=f"gmail:{message['messageId']}",
            fde_id=ctx.fde_id,
            source="gmail",
            date=ctx.window.date,
            ts_utc=ts,
            ts_local=ctx.window.to_local(ts),
            kind="email",
            involvement=involvement,
            actor=actor,
            counterparts=[person for person in recipients if person.email != actor.email],
            location=Location(kind="mailbox", id="SENT" if "SENT" in labels else "INBOX", name="Gmail"),
            thread_id=f"gmail:{message['threadId']}",
            url=message.get("display_url"),
            title=message.get("subject") or headers.get("subject"),
            text=strip_quoted_reply(message.get("messageText") or ""),
            attachments=[
                Attachment(name=item.get("filename") or "attachment", mime_type=item.get("mimeType"))
                for item in message.get("attachmentList") or []
            ],
            meta={"labels": labels, **({"notification_of": notification_of} if notification_of else {})},
            expires_at=ctx.expires_at(ts),
        )
