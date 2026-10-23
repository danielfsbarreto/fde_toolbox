"""Topics: what the crews produce from events, plus the requests sent to them."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from fde_core.domain.events import EventKind, Involvement, Source

Workstream = Literal[
    "pre-sales", "poc", "enablement", "support", "delivery", "internal-initiative", "hiring", "events", "other"
]
MyInvolvement = Literal["led", "contributed", "observed"]
Confidence = Literal["high", "medium", "low"]


def new_id() -> str:
    """Stable id of a topic or follow-up, so the review UI can address single items."""
    return uuid4().hex[:10]


class FollowUp(BaseModel):
    # Not part of the LLM schema (SkipJsonSchema): set by code, kept through edits
    id: SkipJsonSchema[str] = Field(default_factory=new_id)
    reviewed: SkipJsonSchema[bool] = False
    what: str
    owner: str | None = Field(default=None, description="Only when the evidence names the owner")
    due_if_stated: str | None = Field(default=None, description="Only when the evidence states a date")


class TopicDraft(BaseModel):
    """One topic as written by the synthesis crew."""

    title: str
    workstream: Workstream
    what_happened: str
    status_or_decision: str | None = None
    risks: list[str] = []
    my_involvement: MyInvolvement
    follow_ups: list[FollowUp] = []
    people: list[str] = []
    evidence: list[str] = Field(description='Refs of the events that support this topic, e.g. ["e3", "e7"]')
    confidence: Confidence
    inferred: bool = Field(description="True when any statement goes beyond what the evidence states")


class Topic(TopicDraft):
    """A written topic. `evidence` holds stored event ids (refs are mapped back by code)."""

    id: str = Field(default_factory=new_id)
    reviewed: bool = False  # ticked in the review UI
    agent_added: bool = False  # added by the agent in a deeper review
    account_key: str
    attribution: Literal["rules", "content", "review", "manual"] = "rules"
    attribution_confidence: Confidence | None = None
    attribution_reason: str = ""
    flagged: bool = False
    flag_reason: str | None = None


class EventDigest(BaseModel):
    """The view of an event an LLM sees: redacted, truncated text plus a short reference to cite it."""

    ref: str = Field(description='Short reference such as "e12"; cite events by it')
    source: Source
    kind: EventKind
    involvement: Involvement
    time: str
    actor: str
    with_: list[str] = Field(default=[], alias="with")
    where: str | None = None
    title: str | None = None
    text: str = ""

    model_config = {"populate_by_name": True}


class EventCluster(BaseModel):
    """Events of one conversation: a thread, a DM or channel day, a meeting, an email thread, an issue or PR."""

    cluster: str
    current_account: str
    attributed_by: str = Field(default="", description="Why the cluster is in its current account")
    movable: bool = Field(default=False, description="Whether content may move it to another account")
    events: list[EventDigest]


class AccountBrief(BaseModel):
    key: str
    name: str
    description: str = ""
    aliases: list[str] = []
    email_domains: list[str] = []


class TopicGroup(BaseModel):
    title: str = Field(description="Short, specific: what the topic is about")
    workstream: Workstream
    account_key: str = Field(description='An account key, "internal" or "unassigned"')
    event_refs: list[str] = Field(description="Every event of this topic, by ref")
    confidence: Confidence = Field(description="How sure the account attribution is")
    reason: str = Field(default="", description="Why this account, when it differs from the cluster's current account")


class GroupingOutput(BaseModel):
    topics: list[TopicGroup]


class GroupingRequest(BaseModel):
    """The whole day, in one request: every conversation cluster with its rule-based account."""

    output_language: str = "en"
    accounts: list[AccountBrief]
    clusters: list[EventCluster]
    guidance: str = Field(default="None.", description="The FDE's approved learnings for this step, as text")


class SynthesisOutput(BaseModel):
    headline: str = Field(description="One sentence about the account's day")
    topics: list[TopicDraft]


class SynthesisRequest(BaseModel):
    account_key: str
    account_name: str
    account_description: str = ""
    fde_name: str
    output_language: str
    groups: list[TopicGroup]
    events: list[EventDigest]
    feedback: str | None = Field(default=None, description="Why the previous attempt failed validation")
    guidance: str = Field(default="None.", description="The FDE's approved learnings for this step, as text")
