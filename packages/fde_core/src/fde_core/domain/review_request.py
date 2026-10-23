"""The flow's review pause, and what the FDE sends back to it.

Item-level changes are made directly in the review UI (they never go through the flow). The pause is answered
with one of two things: finalize the day, or requests for the agent (look for something missed, take a deeper
look, rework a section), each scoped to the day, an account or a topic.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from fde_core.domain.topics import Confidence, FollowUp, MyInvolvement, Workstream

FEEDBACK_KIND = "fde-review"


class ReviewScope(BaseModel):
    kind: Literal["day", "account", "topic"] = "day"
    key: str | None = Field(default=None, description="The account key or the topic id")


class AgentRequest(BaseModel):
    scope: ReviewScope = ReviewScope()
    instruction: str


class ReviewFeedback(BaseModel):
    """The reply to the review pause. The UI sends it as JSON; plain text is one day-wide request."""

    kind: Literal["fde-review"] = FEEDBACK_KIND
    version: int = 1
    action: Literal["finalize", "requests"]
    requests: list[AgentRequest] = []

    @classmethod
    def parse(cls, reply: str) -> ReviewFeedback:
        text = (reply or "").strip()
        if not text:
            return cls(action="finalize")
        # The app's structured reply, also when pasted into AMP or an email (with a signature or quote around it)
        start = text.find("{")
        while start != -1:
            try:
                data, _ = json.JSONDecoder().raw_decode(text, start)
                if isinstance(data, dict) and data.get("kind") == FEEDBACK_KIND:
                    return cls.model_validate(data)
            except (json.JSONDecodeError, ValidationError):
                pass
            start = text.find("{", start + 1)
        return cls(action="requests", requests=[AgentRequest(instruction=text)])


class ReviewRequest(BaseModel):
    """One pause of the flow, waiting for an answer. The AMP callback is stored encrypted, never in clear."""

    id: str
    fde_id: str
    date: date
    run_id: str
    round: int
    status: Literal["waiting", "answered", "superseded"] = "waiting"
    created_at: datetime
    answered_at: datetime | None = None
    answered_from: str | None = None  # ui, or flow (a reply in AMP, by email or in the terminal)
    amp_request_id: str | None = None
    callback_ciphertext: str | None = None


class ReviewedTopic(BaseModel):
    """A topic as the deep review returns it: a changed existing topic (with its id) or a new one (no id)."""

    id: str | None = Field(default=None, description="The id of the existing topic it changes; empty for a new one")
    title: str
    account_key: str = Field(description='An account key, "internal" or "unassigned"')
    workstream: Workstream
    what_happened: str
    status_or_decision: str | None = None
    risks: list[str] = []
    my_involvement: MyInvolvement
    follow_ups: list[FollowUp] = []
    people: list[str] = []
    evidence: list[str] = Field(description='Refs of the events that support it, e.g. ["e3", "e7"]')
    confidence: Confidence
    inferred: bool = Field(description="True when any statement goes beyond what the evidence states")


class DeepReview(BaseModel):
    """Only what the requests change: rewritten topics in scope, missed topics, and notes on what was not found."""

    changed_topics: list[ReviewedTopic] = []
    new_topics: list[ReviewedTopic] = []
    notes: list[str] = Field(default=[], description="What could not be done or found, one short sentence each")
