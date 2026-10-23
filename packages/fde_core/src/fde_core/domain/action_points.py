"""Action points: the follow-ups of a final daily log, sent to the FDE's task tracker (Todoist) once the day is final.

Each follow-up becomes one task: the FDE's own (no owner, or the FDE) as is, other people's as "Waiting on ...".
Every task gets a due date: the stated one, read against the log's date, or the next working day after the log.
Unless an open task already covers it (one made by hand counts too): then it becomes a comment on that task.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

LABEL = "fde-log"


class ActionPoint(BaseModel):
    key: str = Field(description="Stable id: date, account, follow-up text and owner")
    date: date
    account_key: str
    account_name: str
    what: str
    owner: str | None = None
    mine: bool
    stated_due: str | None = None
    topic_title: str
    links: list[str] = []
    # The follow-ups (by id) this point comes from, so the review app can link each one to its task. Not serialized.
    follow_up_ids: list[str] = Field(default_factory=list, exclude=True)

    @property
    def title(self) -> str:
        return self.what if self.mine else f"Waiting on {self.owner}: {self.what}"


def next_working_day(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


class TaskProject(BaseModel):
    id: str
    name: str
    inbox: bool = False


class TaskSection(BaseModel):
    id: str
    project_id: str
    name: str


class OpenTask(BaseModel):
    """A task still open in the tracker, whoever made it."""

    id: str
    content: str
    description: str = ""
    project_id: str
    section_id: str | None = None
    due_date: str | None = None


class NewTask(BaseModel):
    content: str
    description: str
    project_id: str
    section_id: str | None = None
    labels: list[str] = [LABEL]
    due_date: date


class TaskPlacement(BaseModel):
    """Where and when one action point lands."""

    point: ActionPoint
    target: str = Field(description='"Project / Section" as configured, or "Inbox"')
    project_id: str
    section_id: str | None = None
    due_date: date
    in_inbox: bool = False  # the account has no usable target: the title names the account
    # Where an open task covering it could already be: every usable target of the account, and the Inbox
    scopes: list[tuple[str, str | None]] = []


class ResolvedItem(BaseModel):
    id: str
    target: str | None = Field(default=None, description="One of the item's targets, copied exactly")
    due_date: str | None = Field(default=None, description="YYYY-MM-DD, only when the stated due is a date")


class ActionPointResolution(BaseModel):
    """The resolver's answer: a target for items with several, and a date for stated relative due dates."""

    items: list[ResolvedItem] = []


class ActionItemRecord(BaseModel):
    """A task created in the tracker, kept so a follow-up is never sent twice."""

    fde_id: str
    date: date
    key: str
    account_key: str
    title: str
    task_id: str
    target: str
    due_date: date
    created_at: datetime
    action: Literal["created", "commented"] = "created"  # commented: an open task already covered it
    comment_id: str | None = None
    follow_up_ids: list[str] = []  # the follow-ups that created it (and later copies of them), for the app's links


class SyncSummary(BaseModel):
    status: Literal["ok", "partial", "failed", "skipped"]
    created: int = 0
    commented: int = 0
    mine: int = 0
    waiting: int = 0
    existing: int = 0
    tokens: int = 0
    warnings: list[str] = []
    error: str | None = None

    def line(self) -> str:
        if self.status == "skipped":
            return f"Action points: not sent to Todoist ({'; '.join(self.warnings) or 'skipped'})."
        text = (
            f"Action points: {self.created} new Todoist task{'' if self.created == 1 else 's'} "
            f"({self.mine} yours, {self.waiting} waiting on others), {self.commented} added as a comment to an open "
            f"task, {self.existing} already there."
        )
        if self.error:
            text += f" Failed: {self.error}."
        return " ".join([text, *[f"{warning}." for warning in self.warnings]])


class TaskMatch(BaseModel):
    id: str
    task_id: str | None = Field(
        default=None,
        description="The candidate task that already covers this follow-up, its id copied exactly; empty to create one",
    )
    reason: str = Field(default="", description="One short sentence")


class TaskMatching(BaseModel):
    items: list[TaskMatch] = []
