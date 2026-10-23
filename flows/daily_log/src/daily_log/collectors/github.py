"""GitHub: the FDE's PRs, issues, reviews, comments and pushed commits, plus activity in account repos.

Own activity is searched across every org the connection can see, then kept only in the profile's orgs
(or repos the profile lists): activity elsewhere is personal. The orgs are also scanned for PRs and issues
in repos whose name mentions an owned account.
Push events no longer carry commits, so commits come from the pushed branches for the day.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from daily_log.collectors.common import CollectorErrors, iso_z, parse_ts, run_collector, search_range
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, EventKind, Involvement, Location, Person

APP = "github"
_PER_PAGE = 100


@dataclass
class _Item:
    """A PR or issue found by search, and whether the FDE is involved in it."""

    data: dict[str, Any]
    involved: bool
    repo: str = field(init=False)

    def __post_init__(self) -> None:
        self.repo = "/".join(self.data["repository_url"].split("/")[-2:])

    @property
    def is_pr(self) -> bool:
        return "pull_request" in self.data

    @property
    def number(self) -> int:
        return self.data["number"]

    @property
    def thread_id(self) -> str:
        return f"{self.repo}#{self.number}"


def _is_bot(login: str | None) -> bool:
    return bool(login) and login.endswith("[bot]")


class GitHubCollector:
    source = "github"

    def __init__(self, actions: ActionRunner, max_workers: int = 6, max_search_pages: int = 3) -> None:
        self._actions = actions
        self._max_workers = max_workers
        self._max_search_pages = max_search_pages

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("github", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        login = ctx.identity.github_login
        if not login:
            raise ActionError(APP, "get_the_authenticated_user", "no_login", "GitHub login unknown")
        items = [item for item in self._search_items(ctx, login, errors) if _in_scope(ctx, item.repo)]
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            per_item = list(pool.map(lambda item: self._item_events(ctx, login, item, errors), items))
            commits = self._commit_events(ctx, login, pool, errors)
        events = [event for group in per_item for event in group] + commits
        return list({event.id: event for event in events}.values())

    # Search -------------------------------------------------------------------------------------

    def _search_items(self, ctx: CollectionContext, login: str, errors: CollectorErrors) -> list[_Item]:
        updated = f"updated:{search_range(ctx)}"
        mine = [
            f"is:pr involves:{login} {updated}",
            f"is:issue involves:{login} {updated}",
            f"is:pr reviewed-by:{login} {updated}",
        ]
        scoped = [
            f"is:{kind} org:{org} {updated}" for org in ctx.profile.sources.github.orgs for kind in ("pr", "issue")
        ]
        override_repos = {repo for account in ctx.profile.owned_accounts for repo in account.github_repos}
        scoped += [f"is:{kind} repo:{repo} {updated}" for repo in sorted(override_repos) for kind in ("pr", "issue")]

        items: dict[str, _Item] = {}
        for query in mine:
            for data in self._search(query, errors, required=True):
                items[data["html_url"]] = _Item(data, involved=True)
        for query in scoped:
            for data in self._search(query, errors, required=False):
                item = _Item(data, involved=False)
                if data["html_url"] not in items and self._is_account_repo(ctx, item.repo, override_repos):
                    items[data["html_url"]] = item
        return [item for item in items.values() if not _is_bot(item.data["user"]["login"]) or item.involved]

    def _search(self, query: str, errors: CollectorErrors, required: bool) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in range(1, self._max_search_pages + 1):
            try:
                output = self._actions.run(APP, "search_issues", {"q": query, "per_page": _PER_PAGE, "page": page})
            except ActionError as error:
                if required and page == 1:
                    raise
                errors.record(error)
                break
            batch = output.get("items", [])
            found += batch
            if len(batch) < _PER_PAGE:
                break
        return found

    @staticmethod
    def _is_account_repo(ctx: CollectionContext, repo: str, override_repos: set[str]) -> bool:
        if repo.lower() in override_repos:
            return True
        name = repo.split("/", 1)[1]
        return any(account.names_place(name) for account in ctx.profile.owned_accounts)

    # Item events ---------------------------------------------------------------------------------

    def _item_events(
        self, ctx: CollectionContext, login: str, item: _Item, errors: CollectorErrors
    ) -> list[ActivityEvent]:
        data = item.data
        kind: EventKind = "pr" if item.is_pr else "issue"
        author = data["user"]["login"]
        events: list[ActivityEvent] = []

        created = parse_ts(data["created_at"])
        if created and ctx.window.contains(created):
            events.append(
                self._event(
                    ctx,
                    login,
                    item,
                    f"{kind}:{item.thread_id}:opened",
                    kind,
                    author,
                    created,
                    data.get("body") or "",
                    {"action": "opened"},
                    data["html_url"],
                )
            )
        merged = parse_ts((data.get("pull_request") or {}).get("merged_at"))
        closed = parse_ts(data.get("closed_at"))
        owner, repo = item.repo.split("/")
        if merged and ctx.window.contains(merged):
            merged_by = None
            try:
                pull = self._actions.run(
                    APP, "get_a_pull_request", {"owner": owner, "repo": repo, "pull_number": item.number}
                )
                merged_by = (pull.get("merged_by") or {}).get("login")
            except ActionError as error:
                errors.record(error)
            events.append(
                self._event(
                    ctx,
                    login,
                    item,
                    f"{kind}:{item.thread_id}:merged",
                    kind,
                    merged_by,
                    merged,
                    "",
                    {"action": "merged"},
                    data["html_url"],
                )
            )
        elif closed and ctx.window.contains(closed):
            events.append(
                self._event(
                    ctx,
                    login,
                    item,
                    f"{kind}:{item.thread_id}:closed",
                    kind,
                    None,
                    closed,
                    "",
                    {"action": "closed", "state_reason": data.get("state_reason") or ""},
                    data["html_url"],
                )
            )

        if data.get("comments"):
            try:
                output = self._actions.run(
                    APP,
                    "get_issue_comments",
                    {
                        "owner": owner,
                        "repo": repo,
                        "issue_number": item.number,
                        "since": iso_z(ctx.window.start_utc),
                        "per_page": _PER_PAGE,
                    },
                )
                for comment in output.get("comments", []):
                    ts = parse_ts(comment["created_at"])
                    if ts and ctx.window.contains(ts) and not _is_bot(comment["user"]["login"]):
                        events.append(
                            self._event(
                                ctx,
                                login,
                                item,
                                f"comment:{comment['id']}",
                                "comment",
                                comment["user"]["login"],
                                ts,
                                comment.get("body") or "",
                                {"action": "commented"},
                                comment["html_url"],
                            )
                        )
            except ActionError as error:
                errors.record(error)
        if item.is_pr:
            try:
                output = self._actions.run(
                    APP,
                    "list_reviews_for_a_pull_request",
                    {"owner": owner, "repo": repo, "pull_number": item.number, "per_page": _PER_PAGE},
                )
                for review in output.get("reviews", []):
                    ts = parse_ts(review.get("submitted_at"))
                    reviewer = (review.get("user") or {}).get("login")
                    if ts and ctx.window.contains(ts) and review.get("state") != "PENDING" and not _is_bot(reviewer):
                        events.append(
                            self._event(
                                ctx,
                                login,
                                item,
                                f"review:{review['id']}",
                                "review",
                                reviewer,
                                ts,
                                review.get("body") or "",
                                {"action": "reviewed", "review_state": review["state"]},
                                review.get("html_url"),
                            )
                        )
            except ActionError as error:
                errors.record(error)
        return events

    def _event(
        self,
        ctx: CollectionContext,
        login: str,
        item: _Item,
        native_id: str,
        kind: EventKind,
        actor_login: str | None,
        ts: datetime,
        text: str,
        meta: dict[str, Any],
        url: str | None,
    ) -> ActivityEvent:
        data = item.data
        author = data["user"]["login"]
        assignees = [assignee["login"] for assignee in data.get("assignees") or []]
        involvement = self._involvement(login, item, actor_login, text)
        counterparts = [
            _person(name, login) for name in dict.fromkeys([author, *assignees]) if name and name != actor_login
        ]
        return ActivityEvent(
            id=f"github:{native_id}",
            fde_id=ctx.fde_id,
            source="github",
            date=ctx.window.date,
            ts_utc=ts,
            ts_local=ctx.window.to_local(ts),
            kind=kind,
            involvement=involvement,
            actor=_person(actor_login, login) if actor_login else Person(),
            counterparts=counterparts,
            location=Location(kind="repo", id=item.repo, name=item.repo.split("/")[1]),
            thread_id=item.thread_id,
            url=url,
            title=data["title"],
            text=text,
            meta={
                **meta,
                "number": item.number,
                "state": data.get("state") or "",
                "labels": [label["name"] for label in data.get("labels") or []],
            },
            expires_at=ctx.expires_at(ts),
        )

    @staticmethod
    def _involvement(login: str, item: _Item, actor_login: str | None, text: str) -> Involvement:
        if actor_login == login:
            return "authored"
        if re.search(rf"@{re.escape(login)}\b", text, re.I):
            return "mentioned"
        return "participated" if item.involved else "observed"

    # Commits -------------------------------------------------------------------------------------

    def _commit_events(
        self, ctx: CollectionContext, login: str, pool: ThreadPoolExecutor, errors: CollectorErrors
    ) -> list[ActivityEvent]:
        pushes = {
            branch: heads for branch, heads in self._pushes(ctx, login, errors).items() if _in_scope(ctx, branch[0])
        }
        results = pool.map(
            lambda branch: self._branch_commits(ctx, login, *branch, pushes[branch], errors), sorted(pushes)
        )
        return [event for group in results for event in group]

    def _pushes(
        self, ctx: CollectionContext, login: str, errors: CollectorErrors
    ) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """(repo, branch) -> [(before, head)] for every push the FDE made in the window."""
        pushes: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for page in range(1, 4):
            try:
                output = self._actions.run(
                    APP, "list_events_for_user", {"username": login, "per_page": 100, "page": page}
                )
            except ActionError as error:
                errors.record(error)
                break
            batch = output.get("events", [])
            for event in batch:
                ts = parse_ts(event.get("created_at"))
                payload = event.get("payload") or {}
                ref = payload.get("ref") or ""
                if (
                    event.get("type") == "PushEvent"
                    and ts
                    and ctx.window.contains(ts)
                    and ref.startswith("refs/heads/")
                ):
                    key = (event["repo"]["name"], ref.removeprefix("refs/heads/"))
                    pushes.setdefault(key, []).append((payload.get("before", ""), payload.get("head", "")))
            oldest = min((parse_ts(event.get("created_at")) for event in batch), default=None)
            if len(batch) < 100 or (oldest and oldest < ctx.window.start_utc):
                break
        return pushes

    def _branch_commits(
        self,
        ctx: CollectionContext,
        login: str,
        repo_full: str,
        branch: str,
        pushes: list[tuple[str, str]],
        errors: CollectorErrors,
    ) -> list[ActivityEvent]:
        commits = self._list_branch_commits(ctx, repo_full, branch, pushes, errors)
        events = []
        for commit in commits:
            details = commit.get("commit") or {}
            author_login = (commit.get("author") or {}).get("login")
            author_email = (details.get("author") or {}).get("email")
            ts = parse_ts((details.get("committer") or {}).get("date") or (details.get("author") or {}).get("date"))
            if not ts or not ctx.window.contains(ts) or _is_bot(author_login):
                continue
            is_self = author_login == login or ctx.identity.is_self_email(author_email)
            message = details.get("message") or ""
            events.append(
                ActivityEvent(
                    id=f"github:commit:{repo_full}@{commit['sha']}",
                    fde_id=ctx.fde_id,
                    source="github",
                    date=ctx.window.date,
                    ts_utc=ts,
                    ts_local=ctx.window.to_local(ts),
                    kind="commit",
                    involvement="authored" if is_self else "observed",
                    actor=Person(
                        name=author_login or (details.get("author") or {}).get("name"),
                        email=author_email,
                        handle=author_login,
                        is_self=is_self,
                    ),
                    location=Location(kind="repo", id=repo_full, name=repo_full.split("/")[1]),
                    thread_id=f"{repo_full}@{branch}",
                    url=commit.get("html_url"),
                    title=message.splitlines()[0][:200] if message else None,
                    text=message,
                    meta={"action": "committed", "branch": branch, "sha": commit["sha"][:12]},
                    expires_at=ctx.expires_at(ts),
                )
            )
        return events

    def _list_branch_commits(
        self,
        ctx: CollectionContext,
        repo_full: str,
        branch: str,
        pushes: list[tuple[str, str]],
        errors: CollectorErrors,
    ) -> list[dict[str, Any]]:
        """The branch's commits for the day; if the branch is gone (merged and deleted), each push's range."""
        owner, repo = repo_full.split("/")
        try:
            output = self._actions.run(
                APP,
                "list_commits",
                {
                    "owner": owner,
                    "repo": repo,
                    "sha": branch,
                    "since": iso_z(ctx.window.start_utc),
                    "until": iso_z(ctx.window.end_utc),
                    "per_page": _PER_PAGE,
                },
            )
            return output.get("commits", [])
        except ActionError:
            pass
        commits: dict[str, dict[str, Any]] = {}
        for before, head in pushes:
            try:
                output = self._actions.run(
                    APP, "compare_two_commits", {"owner": owner, "repo": repo, "basehead": f"{before}...{head}"}
                )
                commits.update({commit["sha"]: commit for commit in output.get("commits", [])})
            except ActionError as error:
                errors.record(error)
        return list(commits.values())


def _in_scope(ctx: CollectionContext, repo: str) -> bool:
    """Work repos: the profile's orgs, or repos it lists. Everything else is the FDE's personal activity."""
    orgs = {org.lower() for org in ctx.profile.sources.github.orgs}
    listed = {listed for account in ctx.profile.owned_accounts for listed in account.github_repos}
    return not orgs or repo.split("/", 1)[0].lower() in orgs or repo.lower() in listed


def _person(name: str, login: str) -> Person:
    return Person(name=name, handle=name, is_self=name == login)
