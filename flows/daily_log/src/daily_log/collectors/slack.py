"""Slack: the FDE's messages, DMs and mentions, threads they replied in, and channels about owned accounts.

Account channels are found by name among the channels the FDE
belongs to, plus any channel ids the profile lists. Users are resolved to name and email (cached), so people
from a customer's workspace in Slack Connect channels carry the customer's email domain.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from daily_log.collectors.common import CollectorErrors, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from daily_log.ports.repositories import ReferenceCacheRepository
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, Involvement, Location, Person
from fde_core.domain.text import email_domain

APP = "slack"
_USERS_CACHE = "slack_users"
_SKIPPED_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "bot_message"}
_USER_REF = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_REF = re.compile(r"<#[CG][A-Z0-9]+\|([^>]*)>")
_LINK = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")
_SPECIAL = re.compile(r"<!(?:date\^[^|>]*|subteam\^[^|>]*|[a-z]+)(?:\|([^>]*))?>")


def _ts(value: str) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _thread_ts(match: dict[str, Any]) -> str | None:
    return match.get("thread_ts") or parse_qs(urlparse(match.get("permalink", "")).query).get("thread_ts", [None])[0]


class _Users:
    """Slack user id -> name and email, cached per FDE in reference_cache."""

    def __init__(self, actions: ActionRunner, cache: ReferenceCacheRepository, fde_id: str) -> None:
        self._actions, self._cache, self._fde_id = actions, cache, fde_id
        cached = cache.get(fde_id, _USERS_CACHE)
        self._users: dict[str, dict[str, str | None]] = dict(cached[0]) if cached else {}
        self._changed = False

    def prefetch(self, user_ids: set[str], pool: ThreadPoolExecutor) -> None:
        missing = sorted(user_id for user_id in user_ids if user_id and user_id not in self._users)
        for user_id, info in zip(missing, pool.map(self._lookup, missing), strict=True):
            self._users[user_id] = info
            self._changed = True

    def get(self, user_id: str | None) -> dict[str, str | None]:
        if not user_id:
            return {}
        if user_id not in self._users:
            self._users[user_id] = self._lookup(user_id)
            self._changed = True
        return self._users[user_id]

    def _lookup(self, user_id: str) -> dict[str, str | None]:
        try:
            user = self._actions.run(APP, "retrieve_detailed_user_information", {"user": user_id}).get("user", {})
        except ActionError:
            return {"name": None, "email": None}
        profile = user.get("profile") or {}
        return {
            "name": profile.get("real_name") or user.get("real_name") or user.get("name"),
            "email": (profile.get("email") or "").lower() or None,
            "is_bot": bool(user.get("is_bot")) or user_id == "USLACKBOT",
        }

    def person(self, user_id: str | None, me: str) -> Person:
        info = self.get(user_id)
        email = info.get("email")
        return Person(
            name=info.get("name"), email=email, handle=user_id, domain=email_domain(email), is_self=user_id == me
        )

    def readable(self, text: str) -> str:
        text = _USER_REF.sub(lambda m: f"@{self.get(m[1]).get('name') or m[1]}", text)
        text = _CHANNEL_REF.sub(lambda m: f"#{m[1]}", text)
        text = _SPECIAL.sub(lambda m: m[1] or "", text)
        return _LINK.sub(lambda m: m[2] or m[1].removeprefix("mailto:"), text)

    def is_bot(self, user_id: str | None) -> bool:
        return bool(self.get(user_id).get("is_bot")) if user_id else False

    def save(self) -> None:
        if self._changed:
            self._cache.put(self._fde_id, _USERS_CACHE, self._users)


class SlackCollector:
    source = "slack"

    def __init__(
        self, actions: ActionRunner, cache: ReferenceCacheRepository, max_search_pages: int = 5, max_workers: int = 4
    ) -> None:
        self._actions = actions
        self._cache = cache
        self._max_search_pages = max_search_pages
        self._max_workers = max_workers

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("slack", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        me = ctx.identity.slack_user_id
        if not me:
            raise ActionError(APP, "slack_who_am_i", "no_user", "Slack user unknown")
        users = _Users(self._actions, self._cache, ctx.fde_id)
        workspace = (self._actions.run(APP, "slack_who_am_i").get("data") or {}).get("url", "").rstrip("/")

        # (channel id, ts) -> (message, channel info, how it was found)
        found: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], set[str]]] = {}
        my_threads: set[tuple[str, str]] = set()
        day = ctx.window.date
        span = f"after:{day - timedelta(days=1)} before:{day + timedelta(days=1)}"
        for query, how in (
            (f"from:<@{me}> {span}", "mine"),
            (f"<@{me}> {span}", "mention"),
            (f"to:<@{me}> {span}", "to_me"),
        ):
            for match in self._search(query, errors, required=how == "mine"):
                if not ctx.window.contains(_ts(match["ts"])):
                    continue
                channel = match.get("channel") or {}
                entry = found.setdefault((channel["id"], match["ts"]), (match, channel, set()))
                entry[2].add(how)
                thread_ts = _thread_ts(match)
                if thread_ts:
                    match["thread_ts"] = thread_ts
                    if how == "mine":
                        my_threads.add((channel["id"], thread_ts))

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            # Channel activity comes from one search per channel ("in:<#C…>" for the day), which also returns
            # thread replies: Slack rate-limits history and per-thread fetches hard. Watched (account) channels
            # keep everything; other channels keep only the threads the FDE wrote in. DMs cannot be searched by
            # channel, so threads in DMs are fetched one by one.
            watched = {channel["id"]: channel for channel in self._watched_channels(ctx, errors)}
            thread_channels: dict[str, set[str]] = {}
            for channel_id, thread_ts in my_threads:
                thread_channels.setdefault(channel_id, set()).add(thread_ts)
            searchable = sorted(
                {*watched, *(channel_id for channel_id in thread_channels if channel_id[:1] in ("C", "G"))}
            )
            results = pool.map(lambda channel_id: self._channel_day(channel_id, span, errors), searchable)
            for channel_id, matches in zip(searchable, results, strict=True):
                for match in matches:
                    if not ctx.window.contains(_ts(match["ts"])):
                        continue
                    thread_ts = _thread_ts(match)
                    if thread_ts:
                        match["thread_ts"] = thread_ts
                    if channel_id in watched:
                        found.setdefault(
                            (channel_id, match["ts"]), (match, match.get("channel") or watched[channel_id], {"watched"})
                        )
                    elif thread_ts in thread_channels.get(channel_id, set()):
                        found.setdefault(
                            (channel_id, match["ts"]), (match, match.get("channel") or {"id": channel_id}, set())
                        )
            channels_by_id = {key[0]: info[1] for key, info in found.items()}
            direct = sorted(key for key in my_threads if key[0] not in searchable)
            replies = pool.map(lambda key: self._replies(ctx, key[0], key[1], errors), direct)
            for key, thread_replies in zip(direct, replies, strict=True):
                for reply in thread_replies:
                    found.setdefault((key[0], reply["ts"]), (reply, channels_by_id.get(key[0], {"id": key[0]}), set()))
            mentioned = {
                ref for message, _, _ in found.values() for ref in _USER_REF.findall(message.get("text") or "")
            }
            users.prefetch({message.get("user") for message, _, _ in found.values()} | mentioned, pool)

        events = [
            event
            for message, channel, how in found.values()
            if (event := self._event(ctx, me, users, workspace, message, channel, how, my_threads))
        ]
        users.save()
        return events

    # Fetching -----------------------------------------------------------------------------------

    def _search(self, query: str, errors: CollectorErrors, required: bool) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for page in range(1, self._max_search_pages + 1):
            try:
                output = self._actions.run(
                    APP, "search-messages", {"query": query, "count": 100, "page": page, "sort": "timestamp"}
                )
            except ActionError as error:
                if required and page == 1:
                    raise
                errors.record(error)
                break
            block = output.get("messages") or {}
            matches += block.get("matches", [])
            if page >= (block.get("paging") or {}).get("pages", 1):
                break
        return matches

    def _channel_day(self, channel_id: str, span: str, errors: CollectorErrors) -> list[dict[str, Any]]:
        return self._search(f"in:<#{channel_id}> {span}", errors, required=False)

    def _watched_channels(self, ctx: CollectionContext, errors: CollectorErrors) -> list[dict[str, Any]]:
        overrides = {
            *[channel for refs in ctx.references.accounts.values() for channel in refs.slack_channel_ids],
            *ctx.references.internal_slack_channel_ids,
        }
        channels: list[dict[str, Any]] = []
        cursor = None
        while True:
            arguments: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
                "exclude_archived": True,
            }
            if cursor:
                arguments["cursor"] = cursor
            try:
                output = self._actions.run(APP, "list_conversations", arguments)
            except ActionError as error:
                errors.record(error)
                break
            channels += output.get("channels", [])
            cursor = (output.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return [
            channel
            for channel in channels
            if channel["id"] in overrides
            or any(account.names_place(channel.get("name", "")) for account in ctx.profile.owned_accounts)
        ]

    def _replies(
        self, ctx: CollectionContext, channel_id: str, thread_ts: str, errors: CollectorErrors
    ) -> list[dict[str, Any]]:
        replies = self._paged(
            "fetch_message_thread_from_a_conversation",
            {"channel": channel_id, "ts": thread_ts, **self._bounds(ctx)},
            errors,
        )
        return [reply for reply in replies if reply.get("ts") != thread_ts or ctx.window.contains(_ts(thread_ts))]

    @staticmethod
    def _bounds(ctx: CollectionContext) -> dict[str, Any]:
        return {
            "oldest": f"{ctx.window.start_utc.timestamp():.6f}",
            "latest": f"{ctx.window.end_utc.timestamp():.6f}",
            "inclusive": True,
            "limit": 200,
        }

    def _paged(self, action: str, arguments: dict[str, Any], errors: CollectorErrors) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            try:
                output = self._actions.run(APP, action, {**arguments, **({"cursor": cursor} if cursor else {})})
            except ActionError as error:
                errors.record(error)
                break
            messages += output.get("messages", [])
            cursor = (output.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return messages

    # Events -------------------------------------------------------------------------------------

    def _event(
        self,
        ctx: CollectionContext,
        me: str,
        users: _Users,
        workspace: str,
        message: dict[str, Any],
        channel: dict[str, Any],
        how: set[str],
        my_threads: set[tuple[str, str]],
    ) -> ActivityEvent | None:
        ts = _ts(message["ts"])
        if not ctx.window.contains(ts) or message.get("bot_id") or message.get("subtype") in _SKIPPED_SUBTYPES:
            return None
        if users.is_bot(message.get("user")):  # app DMs: Google Drive, Calendar, Slackbot...
            return None
        channel_id = channel["id"]
        actor = users.person(message.get("user"), me)
        is_dm = bool(channel.get("is_im"))
        is_group_dm = bool(channel.get("is_mpim"))
        thread_ts = message.get("thread_ts")
        text = users.readable(message.get("text") or "")
        counterparts: list[Person] = []
        if is_dm:
            other = channel.get("user") or (
                channel.get("name") if str(channel.get("name", "")).startswith("U") else None
            )
            if other and other != actor.handle:
                counterparts.append(users.person(other, me))
        name = channel.get("name")
        if is_dm:
            name = f"DM with {(counterparts[0].name if counterparts else None) or actor.name or 'someone'}"
        elif is_group_dm:
            name = "group DM"

        involvement: Involvement
        if actor.is_self:
            involvement = "authored"
        elif f"<@{me}>" in (message.get("text") or "") or "mention" in how:
            involvement = "mentioned"
        elif is_dm or is_group_dm or "to_me" in how or (channel_id, thread_ts) in my_threads:
            involvement = "participated"
        else:
            involvement = "observed"
        permalink = message.get("permalink") or (
            f"{workspace}/archives/{channel_id}/p{message['ts'].replace('.', '')}" if workspace else None
        )
        return ActivityEvent(
            id=f"slack:{channel_id}:{message['ts']}",
            fde_id=ctx.fde_id,
            source="slack",
            date=ctx.window.date,
            ts_utc=ts,
            ts_local=ctx.window.to_local(ts),
            kind="message",
            involvement=involvement,
            actor=actor,
            counterparts=counterparts,
            location=Location(
                kind="dm" if is_dm else "group_dm" if is_group_dm else "channel", id=channel_id, name=name
            ),
            thread_id=f"slack:{channel_id}:{thread_ts}" if thread_ts else f"slack:{channel_id}",
            url=permalink,
            title=None,
            text=text,
            meta={
                "action": "messaged",
                "shared_channel": bool(channel.get("is_ext_shared")),
                "in_thread": bool(thread_ts),
            },
            expires_at=ctx.expires_at(ts),
        )
