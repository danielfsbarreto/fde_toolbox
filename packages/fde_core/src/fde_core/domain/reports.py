"""Account reports: what the FDE asked for, and the report the account_report flow wrote from final days.

The review app saves the request (status "queued") and starts the flow with the report's id; the flow writes the
content and the status. Only final days are read: they are the record of what happened. The review app draws the
PDF from the content, so the design can change without running the LLM again.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from fde_core.domain.topics import new_id

ReportStatus = Literal["queued", "running", "done", "failed"]
LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}


class ReportRequest(BaseModel):
    accounts: list[str] = Field(min_length=1, description="Owned account keys")
    start: date
    end: date
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    comment: str = Field(default="", description="The report's goal or audience, from the FDE; shifts emphasis only")


class Development(BaseModel):
    date: date
    text: str
    links: list[str] = []  # the evidence of the topics it cites


class OpenFollowUp(BaseModel):
    date: date
    what: str
    owner: str | None = None
    due: str | None = None
    todoist: Literal["open", "completed", "not_sent", "unknown"] = "not_sent"
    url: str | None = None


class AccountSection(BaseModel):
    account_key: str
    account_name: str
    summary: str
    developments: list[Development] = []
    decisions: list[str] = []
    risks: list[str] = []
    follow_ups: list[OpenFollowUp] = []  # from the days' follow-ups, with their Todoist status (code, not the LLM)
    days_with_activity: list[date] = []


class ReportContent(BaseModel):
    title: str
    overview: str
    sections: list[AccountSection] = []
    days_covered: list[date] = []  # final days in the range
    days_missing: list[date] = []  # weekdays in the range without a final day


class Report(BaseModel):
    id: str = Field(default_factory=new_id)
    fde_id: str
    request: ReportRequest
    status: ReportStatus = "queued"
    error: str | None = None
    kickoff_id: str | None = None
    content: ReportContent | None = None
    tokens: int = 0
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
