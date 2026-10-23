"""Linear: issues tied to an owned account through the Customer field, plus issues the FDE created, owns or
commented on. Events are what happened that day: created, moved to another state, reassigned, commented.

For account attribution only the Customer link counts (matching rule 1); the customer ids ride in `meta`.
"""

from __future__ import annotations

from typing import Any

from daily_log.collectors.common import CollectorErrors, iso_z, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, EventKind, Involvement, Location, Person
from fde_core.domain.text import email_domain

APP = "linear"
_ISSUE_FIELDS = """nodes { id identifier title url description createdAt
  state { name type } assignee { name email } creator { name email } team { key name } project { name }
  labels(first: 10) { nodes { name } }
  needs(first: 10) { nodes { customer { id name } } }
  comments(first: 50, filter: { createdAt: { gte: $start, lt: $end } }) {
    nodes { id body createdAt url user { name email } }
  }
  history(first: 50) {
    nodes { id createdAt actor { name email } fromState { name } toState { name }
      fromAssignee { name } toAssignee { name } }
  }
} pageInfo { hasNextPage endCursor }"""
_BY_CUSTOMER = (
    "query($start: DateTimeOrDuration!, $end: DateTimeOrDuration!, $customers: [ID!], $after: String) {"
    " issues(first: 50, after: $after, filter: { updatedAt: { gte: $start, lt: $end },"
    " needs: { some: { customer: { id: { in: $customers } } } } }) { " + _ISSUE_FIELDS + " } }"
)
_MINE = (
    "query($start: DateTimeOrDuration!, $end: DateTimeOrDuration!, $after: String) {"
    " issues(first: 50, after: $after, filter: { updatedAt: { gte: $start, lt: $end }, or: ["
    " { creator: { isMe: { eq: true } } }, { assignee: { isMe: { eq: true } } },"
    " { comments: { some: { user: { isMe: { eq: true } } } } } ] }) { " + _ISSUE_FIELDS + " } }"
)


def _person(user: dict[str, Any] | None, ctx: CollectionContext) -> Person:
    user = user or {}
    email = (user.get("email") or "").lower() or None
    return Person(
        name=user.get("name"), email=email, domain=email_domain(email), is_self=ctx.identity.is_self_email(email)
    )


class LinearCollector:
    source = "linear"

    def __init__(self, actions: ActionRunner) -> None:
        self._actions = actions

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("linear", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        bounds = {"start": iso_z(ctx.window.start_utc), "end": iso_z(ctx.window.end_utc)}
        customers = sorted({cid for refs in ctx.references.accounts.values() for cid in refs.linear_customer_ids})
        issues: dict[str, tuple[dict[str, Any], bool]] = {}
        for issue in self._issues(_MINE, bounds, errors, required=True):
            issues[issue["id"]] = (issue, True)
        if customers:
            for issue in self._issues(_BY_CUSTOMER, {**bounds, "customers": customers}, errors, required=False):
                issues.setdefault(issue["id"], (issue, False))
        return [event for issue, mine in issues.values() for event in self._issue_events(ctx, issue, mine)]

    def _issues(
        self, query: str, variables: dict[str, Any], errors: CollectorErrors, required: bool
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        after = None
        for _ in range(10):
            try:
                output = self._actions.run(
                    APP,
                    "run_query_or_mutation",
                    {"query_or_mutation": query, "variables": {**variables, "after": after}},
                )
            except ActionError as error:
                if required and not issues:
                    raise
                errors.record(error)
                break
            if output.get("errors"):
                message = str(output["errors"][0].get("message", "GraphQL error"))[:160]
                error = ActionError(APP, "run_query_or_mutation", "graphql", message)
                if required and not issues:
                    raise error
                errors.record(error)
                break
            page = (output.get("data") or {}).get("issues") or {}
            issues += page.get("nodes", [])
            if not (page.get("pageInfo") or {}).get("hasNextPage"):
                break
            after = page["pageInfo"]["endCursor"]
        return issues

    def _issue_events(self, ctx: CollectionContext, issue: dict[str, Any], mine: bool) -> list[ActivityEvent]:
        assignee = _person(issue.get("assignee"), ctx)
        creator = _person(issue.get("creator"), ctx)
        involved = mine or assignee.is_self or creator.is_self
        events: list[ActivityEvent] = []

        created = parse_ts(issue.get("createdAt"))
        if created and ctx.window.contains(created):
            events.append(
                self._event(ctx, issue, "created", "issue", creator, created, issue.get("description") or "", involved)
            )
        for change in (issue.get("history") or {}).get("nodes", []):
            ts = parse_ts(change.get("createdAt"))
            if not ts or not ctx.window.contains(ts):
                continue
            actor = _person(change.get("actor"), ctx)
            same_state = (change.get("fromState") or {}).get("name") == (change.get("toState") or {}).get("name")
            if change.get("toState") and not same_state:
                action = f"moved to {change['toState']['name']}"
                if change.get("fromState"):
                    action = f"moved from {change['fromState']['name']} to {change['toState']['name']}"
                events.append(self._event(ctx, issue, action, "issue", actor, ts, "", involved, change["id"]))
            elif not change.get("toState") and (change.get("toAssignee") or change.get("fromAssignee")):
                target = (change.get("toAssignee") or {}).get("name") or "nobody"
                events.append(
                    self._event(ctx, issue, f"assigned to {target}", "issue", actor, ts, "", involved, change["id"])
                )
        for comment in (issue.get("comments") or {}).get("nodes", []):
            ts = parse_ts(comment.get("createdAt"))
            if ts and ctx.window.contains(ts):
                author = _person(comment.get("user"), ctx)
                events.append(
                    self._event(
                        ctx,
                        issue,
                        "commented",
                        "comment",
                        author,
                        ts,
                        comment.get("body") or "",
                        involved,
                        comment["id"],
                        comment.get("url"),
                    )
                )
        return events

    @staticmethod
    def _event(
        ctx: CollectionContext,
        issue: dict[str, Any],
        action: str,
        kind: EventKind,
        actor: Person,
        ts,
        text: str,
        involved: bool,
        native_id: str | None = None,
        url: str | None = None,
    ) -> ActivityEvent:
        involvement: Involvement = "authored" if actor.is_self else "participated" if involved else "observed"
        identifier = issue["identifier"]
        team = issue.get("team") or {}
        return ActivityEvent(
            id=f"linear:{identifier}:{native_id or action.split()[0]}",
            fde_id=ctx.fde_id,
            source="linear",
            date=ctx.window.date,
            ts_utc=ts,
            ts_local=ctx.window.to_local(ts),
            kind=kind,
            involvement=involvement,
            actor=actor,
            counterparts=[
                person for person in (_person(issue.get("assignee"), ctx),) if person.name and person.name != actor.name
            ],
            location=Location(kind="issue", id=identifier, name=team.get("name") or team.get("key")),
            thread_id=f"linear:{identifier}",
            url=url or issue.get("url"),
            title=f"{identifier} {issue.get('title') or ''}".strip(),
            text=text,
            meta={
                "action": action,
                "state": (issue.get("state") or {}).get("name") or "",
                "project": (issue.get("project") or {}).get("name") or "",
                "labels": [label["name"] for label in (issue.get("labels") or {}).get("nodes", [])],
                "linear_customer_ids": [
                    need["customer"]["id"]
                    for need in (issue.get("needs") or {}).get("nodes", [])
                    if need.get("customer")
                ],
            },
            expires_at=ctx.expires_at(ts),
        )
