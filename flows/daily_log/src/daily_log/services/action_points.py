"""Action points from a final daily log, and where and when each one lands in the task tracker.

Extraction and placement are plain code. One resolver call per day (only when needed) picks a target for accounts
with several, and reads stated relative due dates ("tomorrow", "over the week") against the log's date; its answer
is checked here, with the first target and the next working day as fallbacks. One matcher call then decides, per
follow-up, whether an open task where it would land already covers it (a comment then, not a new task); only that
follow-up's own candidates are accepted.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from daily_log.ports.llm import (
    ActionPointItem,
    ActionPointRequest,
    ActionPointResolver,
    TaskMatchCandidate,
    TaskMatcher,
    TaskMatchItem,
    TaskMatchRequest,
)
from daily_log.services.errors import describe_error
from fde_core.domain.action_points import (
    ActionPoint,
    NewTask,
    OpenTask,
    TaskPlacement,
    TaskProject,
    TaskSection,
    next_working_day,
)
from fde_core.domain.daily_log import DailyLog
from fde_core.domain.events import INTERNAL, UNASSIGNED
from fde_core.domain.learnings import FOLLOW_UPS, Guidance
from fde_core.domain.profile import FdeProfile, TodoistTarget
from fde_core.domain.text import fold
from fde_core.domain.topics import Topic

INBOX = "Inbox"
MAX_DUE_DAYS = 90
_SELF = {"me", "i", "eu", "yo"}


def _is_mine(owner: str | None, profile: FdeProfile) -> bool:
    if not owner or not owner.strip():
        return True
    folded = fold(owner)
    return folded in _SELF or fold(profile.name) in folded or any(email in folded for email in profile.emails)


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value else None
    except ValueError:
        return None


def _in_range(due: date | None, day: date) -> date | None:
    return due if due and day <= due <= day + timedelta(days=MAX_DUE_DAYS) else None


def describe(placement: TaskPlacement) -> NewTask:
    point = placement.point
    lines = [f"{point.account_name} · {point.topic_title} · daily log {point.date.isoformat()}", *point.links]
    if point.stated_due and _iso(point.stated_due) is None:
        lines.append(f"Due as stated: {point.stated_due}")
    return NewTask(
        content=f"{point.account_name}: {point.title}" if placement.in_inbox else point.title,
        description="\n".join(lines),
        project_id=placement.project_id,
        section_id=placement.section_id,
        due_date=placement.due_date,
    )


def comment_on(placement: TaskPlacement) -> str:
    """What is added to an open task that already covers a follow-up."""
    point = placement.point
    lines = [
        f"From the daily log of {point.date.isoformat()} ({point.account_name}): {point.title}",
        f"Topic: {point.topic_title}",
    ]
    return "\n".join([*lines, *point.links])


Scope = tuple[str, str | None]


class ActionPointService:
    def __init__(self, resolver: ActionPointResolver, matcher: TaskMatcher | None = None) -> None:
        self._resolver = resolver
        self._matcher = matcher

    def extract(self, log: DailyLog, profile: FdeProfile) -> list[ActionPoint]:
        """Every follow-up of every topic in the log (manual additions included), once per key."""
        links = {event_id: line.url for lines in log.activity.values() for line in lines for event_id in line.event_ids}
        sections: list[tuple[str, str, list[Topic]]] = [
            (digest.account_key, digest.account_name, digest.topics) for digest in log.accounts
        ]
        sections += [(INTERNAL, "Internal", log.internal), (UNASSIGNED, "Unassigned", log.unassigned)]
        points: dict[str, ActionPoint] = {}
        for account_key, account_name, topics in sections:
            for topic in topics:
                urls = list(dict.fromkeys(url for event_id in topic.evidence if (url := links.get(event_id))))
                for follow_up in topic.follow_ups:
                    owner = (follow_up.owner or "").strip() or None
                    raw = f"{log.date.isoformat()}|{account_key}|{fold(follow_up.what)}|{fold(owner or '')}"
                    key = hashlib.sha256(raw.encode()).hexdigest()[:16]
                    point = points.setdefault(
                        key,
                        ActionPoint(
                            key=key,
                            date=log.date,
                            account_key=account_key,
                            account_name=account_name,
                            what=follow_up.what.strip(),
                            owner=owner,
                            mine=_is_mine(owner, profile),
                            stated_due=(follow_up.due_if_stated or "").strip() or None,
                            topic_title=topic.title,
                            links=urls,
                        ),
                    )
                    if follow_up.id not in point.follow_up_ids:
                        point.follow_up_ids.append(follow_up.id)
        return list(points.values())

    def place(
        self,
        points: list[ActionPoint],
        profile: FdeProfile,
        projects: list[TaskProject],
        sections: list[TaskSection],
        day: date,
        guidance: Guidance | None = None,
    ) -> tuple[list[TaskPlacement], list[str], int]:
        """Placements for the points, warnings for targets that could not be used, and the resolver's tokens."""
        warnings: list[str] = []
        inbox = next((project for project in projects if project.inbox), None)
        if inbox is None:
            raise ValueError("Todoist has no Inbox project")
        rows = {account.key: account.todoist for account in profile.owned_accounts} | {
            INTERNAL: profile.internal.todoist
        }
        usable: dict[str, list[tuple[str, str, str | None]]] = {}
        for account_key in {point.account_key for point in points}:
            usable[account_key] = []
            for target in rows.get(account_key, []):
                ids = self._ids(target, projects, sections)
                if ids is None:
                    warnings.append(f"Todoist target '{target}' not found")
                else:
                    usable[account_key].append((str(target), *ids))
            if not usable[account_key] and account_key != UNASSIGNED:
                warnings.append(f"{account_key} has no usable Todoist row, sent to the Inbox")

        items = []
        for number, point in enumerate(points, start=1):
            targets = usable[point.account_key]
            stated = point.stated_due if point.stated_due and _iso(point.stated_due) is None else None
            if len(targets) > 1 or stated:
                items.append(
                    ActionPointItem(
                        id=f"A{number}",
                        follow_up=point.title,
                        topic=point.topic_title,
                        stated_due=stated,
                        targets=[name for name, _, _ in targets] if len(targets) > 1 else [],
                    )
                )
        resolved, tokens = {}, 0
        if items:
            try:
                result = self._resolver.resolve(
                    ActionPointRequest(
                        day=day.isoformat(),
                        weekday=day.strftime("%A"),
                        items=items,
                        guidance=(guidance or Guidance()).text(FOLLOW_UPS, {point.account_key for point in points}),
                    )
                )
                resolved, tokens = {item.id: item for item in result.output.items}, result.total_tokens
            except Exception as error:  # fall back to the first target and the next working day
                warnings.append(f"due dates and sections not resolved ({describe_error(error)})")

        placements = []
        for number, point in enumerate(points, start=1):
            answer = resolved.get(f"A{number}")
            targets = usable[point.account_key]
            chosen = next((target for target in targets if answer and target[0] == answer.target), None)
            name, project_id, section_id = chosen or (targets[0] if targets else (INBOX, inbox.id, None))
            due = (
                _in_range(_iso(point.stated_due), day)
                or _in_range(_iso(answer.due_date if answer else None), day)
                or next_working_day(day)
            )
            placements.append(
                TaskPlacement(
                    point=point,
                    target=name,
                    project_id=project_id,
                    section_id=section_id,
                    due_date=due,
                    in_inbox=not targets,
                    scopes=list(
                        dict.fromkeys([*((project, section) for _, project, section in targets), (inbox.id, None)])
                    ),
                )
            )
        return placements, warnings, tokens

    def match(
        self,
        placements: list[TaskPlacement],
        open_tasks: dict[Scope, list[OpenTask]],
        day: date,
        guidance: Guidance | None = None,
    ) -> tuple[dict[int, OpenTask], list[str], int]:
        """Per placement (by index), the open task that already covers it; warnings; the matcher's tokens.
        Without a matcher, or when it fails, nothing matches: every follow-up becomes a new task."""
        items, candidates = [], {}
        for index, placement in enumerate(placements):
            pool = {task.id: task for scope in placement.scopes for task in open_tasks.get(scope, [])}
            if not pool:
                continue
            candidates[f"F{index + 1}"] = (index, pool)
            items.append(
                TaskMatchItem(
                    id=f"F{index + 1}",
                    follow_up=placement.point.title,
                    topic=placement.point.topic_title,
                    account=placement.point.account_name,
                    candidates=[
                        TaskMatchCandidate(
                            id=task.id, content=task.content, description=task.description, due_date=task.due_date
                        )
                        for task in pool.values()
                    ],
                )
            )
        if not items or self._matcher is None:
            return {}, [], 0
        try:
            accounts = {placement.point.account_key for placement in placements}
            result = self._matcher.match(
                TaskMatchRequest(
                    day=day.isoformat(), items=items, guidance=(guidance or Guidance()).text(FOLLOW_UPS, accounts)
                )
            )
        except Exception as error:
            return {}, [f"open tasks not checked, all sent as new ({describe_error(error)})"], 0
        matched = {}
        for answer in result.output.items:
            index, pool = candidates.get(answer.id, (None, {}))
            if index is not None and answer.task_id and answer.task_id in pool:
                matched[index] = pool[answer.task_id]
        return matched, [], result.total_tokens

    @staticmethod
    def _ids(
        target: TodoistTarget, projects: list[TaskProject], sections: list[TaskSection]
    ) -> tuple[str, str | None] | None:
        project = next((item for item in projects if fold(item.name) == fold(target.project)), None)
        if project is None:
            return None
        if target.section is None:
            return project.id, None
        section = next(
            (item for item in sections if item.project_id == project.id and fold(item.name) == fold(target.section)),
            None,
        )
        return (project.id, section.id) if section else None
