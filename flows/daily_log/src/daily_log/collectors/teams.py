"""Microsoft Teams: chats (1:1, group, meeting) and channel posts active that day, through the FDE's connection.

Search finds the chats and channels with activity around the day; each chat's messages for the day are then
listed in full. Chat members carry email and tenant, so a group chat with a customer matches by domain.
"""

from __future__ import annotations

import html
import re
from datetime import timedelta
from typing import Any

from daily_log.collectors.common import CollectorErrors, iso_z, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, Attachment, CollectionResult, Involvement, Location, Person
from fde_core.domain.text import email_domain

APP = "microsoft_teams"
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<(br|/p|/div|/li)\s*/?>", re.I)


def _plain(body: dict[str, Any] | None) -> str:
    content = (body or {}).get("content") or ""
    if (body or {}).get("contentType") == "html":
        content = _TAG.sub("", _BREAK.sub("\n", content))
    return re.sub(r"\n{3,}", "\n\n", html.unescape(content)).strip()


class TeamsCollector:
    source = "teams"

    def __init__(self, actions: ActionRunner, max_search_pages: int = 8) -> None:
        self._actions = actions
        self._max_search_pages = max_search_pages

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("teams", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        me = ctx.identity.teams_user_id
        if not me:
            raise ActionError(APP, "microsoft_teams_who_am_i", "no_user", "Teams user unknown")
        chat_ids, channels = self._active(ctx, errors)
        events: list[ActivityEvent] = []
        for chat_id in sorted(chat_ids):
            if chat_id.startswith("48:"):  # the FDE's own notes chat; the API refuses to list it
                continue
            events += self._chat_events(ctx, me, chat_id, errors)
        for team_id, channel_id in sorted(channels):
            events += self._channel_events(ctx, me, team_id, channel_id, errors)
        return events

    def _active(self, ctx: CollectionContext, errors: CollectorErrors) -> tuple[set[str], set[tuple[str, str]]]:
        day = ctx.window.date
        query = f"sent>={day - timedelta(days=1)} AND sent<={day + timedelta(days=1)}"
        chats: set[str] = set()
        channels: set[tuple[str, str]] = set()
        for page in range(self._max_search_pages):
            try:
                output = self._actions.run(
                    APP, "search_messages", {"query": query, "size": 25, "from_index": page * 25}
                )
            except ActionError as error:
                if page == 0:
                    raise
                errors.record(error)
                break
            container = ((output.get("value") or [{}])[0].get("hitsContainers") or [{}])[0]
            for hit in container.get("hits", []):
                resource = hit.get("resource") or {}
                identity = resource.get("channelIdentity") or {}
                if identity.get("teamId") and identity.get("channelId"):
                    channels.add((identity["teamId"], identity["channelId"]))
                elif resource.get("chatId"):
                    chats.add(resource["chatId"])
            if not container.get("moreResultsAvailable"):
                break
        return chats, channels

    def _chat_events(
        self, ctx: CollectionContext, me: str, chat_id: str, errors: CollectorErrors
    ) -> list[ActivityEvent]:
        try:
            chat = self._actions.run(APP, "microsoft_teams_get_chat", {"chat_id": chat_id})
            members = self._actions.run(
                APP, "microsoft_teams_list_user_chat_members", {"user_id": me, "chat_id": chat_id}
            )
        except ActionError as error:
            errors.record(error)
            return []
        people = {
            member.get("userId"): Person(
                name=member.get("displayName"),
                email=(member.get("email") or "").lower() or None,
                domain=email_domain(member.get("email")),
                tenant_id=member.get("tenantId"),
                handle=member.get("userId"),
                is_self=member.get("userId") == me,
            )
            for member in members.get("value", [])
        }
        chat_type = chat.get("chatType", "")
        others = [person for user_id, person in people.items() if user_id != me]
        name = chat.get("topic") or ", ".join(filter(None, (person.name for person in others[:4]))) or "chat"
        location = Location(kind="dm" if chat_type == "oneOnOne" else "group_dm", id=chat_id, name=name)

        messages: list[dict[str, Any]] = []
        page_token = None
        # Edits and reactions bump lastModifiedDateTime, so allow a margin after the day; createdDateTime decides.
        upper = iso_z(ctx.window.end_utc + timedelta(days=2))
        bounds = f"lastModifiedDateTime gt {iso_z(ctx.window.start_utc)} and lastModifiedDateTime lt {upper}"
        for _ in range(10):
            arguments: dict[str, Any] = {
                "user_id": me,
                "chat_id": chat_id,
                "top": 50,
                "filter": bounds,
                "orderby": "lastModifiedDateTime desc",
            }
            if page_token:
                arguments["page_token"] = page_token
            try:
                output = self._actions.run(APP, "microsoft_teams_list_user_chat_messages", arguments)
            except ActionError as error:
                errors.record(error)
                break
            messages += output.get("value", [])
            page_token = output.get("next_page_token")
            if not page_token:
                break
        return [
            event
            for message in messages
            if (event := self._event(ctx, me, message, location, people, f"teams:{chat_id}", participated=True))
        ]

    def _channel_events(
        self, ctx: CollectionContext, me: str, team_id: str, channel_id: str, errors: CollectorErrors
    ) -> list[ActivityEvent]:
        try:
            output = self._actions.run(
                APP, "get_messages", {"team_id": team_id, "channel_id": channel_id, "top": 50, "expand": "replies"}
            )
        except ActionError as error:
            errors.record(error)
            return []
        location = Location(kind="channel", id=channel_id, name=None)
        events = []
        for post in output.get("value", []):
            location.name = location.name or (post.get("channelIdentity") or {}).get("channelId")
            thread = f"teams:{channel_id}:{post.get('id')}"
            for message in [post, *(post.get("replies") or [])]:
                event = self._event(ctx, me, message, location, {}, thread, participated=False)
                if event:
                    events.append(event)
        return events

    def _event(
        self,
        ctx: CollectionContext,
        me: str,
        message: dict[str, Any],
        location: Location,
        people: dict[str | None, Person],
        thread_id: str,
        participated: bool,
    ) -> ActivityEvent | None:
        ts = parse_ts(message.get("createdDateTime"))
        if ts is None or not ctx.window.contains(ts) or message.get("messageType") != "message":
            return None
        if message.get("deletedDateTime"):
            return None
        sender = ((message.get("from") or {}).get("user")) or {}
        sender_id = sender.get("id")
        actor = people.get(sender_id) or Person(
            name=sender.get("displayName"), handle=sender_id, tenant_id=sender.get("tenantId"), is_self=sender_id == me
        )
        mentioned = any(
            ((m.get("mentioned") or {}).get("user") or {}).get("id") == me for m in message.get("mentions") or []
        )
        involvement: Involvement = (
            "authored"
            if actor.is_self
            else "mentioned"
            if mentioned
            else "participated"
            if participated
            else "observed"
        )
        counterparts = [person for user_id, person in people.items() if user_id not in (sender_id, me)]
        return ActivityEvent(
            id=f"teams:{location.id}:{message['id']}",
            fde_id=ctx.fde_id,
            source="teams",
            date=ctx.window.date,
            ts_utc=ts,
            ts_local=ctx.window.to_local(ts),
            kind="message",
            involvement=involvement,
            actor=actor,
            counterparts=counterparts,
            location=location.model_copy(),
            thread_id=thread_id,
            url=message.get("webUrl"),
            title=message.get("subject") or None,
            text=_plain(message.get("body")),
            attachments=[
                Attachment(name=item.get("name") or "attachment", mime_type=item.get("contentType"))
                for item in message.get("attachments") or []
                if item.get("name")
            ],
            meta={"action": "messaged"},
            expires_at=ctx.expires_at(ts),
        )
