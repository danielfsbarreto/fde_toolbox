"""What the LLM writes. Code checks it (dates, cited topics) and turns it into fde_core's ReportContent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DevelopmentDraft(BaseModel):
    date: str = Field(description="YYYY-MM-DD: the day it happened, one of the days given")
    text: str = Field(description="One or two sentences: what happened and why it matters")
    topics: list[str] = Field(description='The topics it comes from, by reference, e.g. ["T3", "T7"]')


class SectionDraft(BaseModel):
    summary: str = Field(description="3 to 5 sentences: where the account stands at the end of the period")
    developments: list[DevelopmentDraft] = Field(description="The key developments, in date order")
    decisions: list[str] = Field(default=[], description="Decisions taken in the period, one sentence each")
    risks: list[str] = Field(default=[], description="Risks and open issues at the end of the period")


class OverviewDraft(BaseModel):
    title: str = Field(description="A short title for the report")
    overview: str = Field(description="One to three paragraphs across the accounts, for the report's reader")
