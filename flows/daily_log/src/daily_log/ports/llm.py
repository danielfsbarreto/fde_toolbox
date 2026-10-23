"""LLM ports, implemented by the crews. Services call these without importing CrewAI."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from fde_core.domain.action_points import ActionPointResolution, TaskMatching
from fde_core.domain.review_request import DeepReview
from fde_core.domain.topics import GroupingOutput, GroupingRequest, SynthesisOutput, SynthesisRequest


class GroupingResult(BaseModel):
    output: GroupingOutput
    total_tokens: int = 0


class SynthesisResult(BaseModel):
    output: SynthesisOutput
    total_tokens: int = 0


class DeepReviewRequest(BaseModel):
    instruction: str
    scope: str = Field(description='What the request covers, e.g. "the whole day" or "the <CUSTOMER> account"')
    accounts: str = Field(description="The accounts, as text")
    topics: str = Field(description="The topics in scope, with ids and evidence refs, as text")
    events: str = Field(description="The events in scope, cited by ref, as text")
    output_language: str = "English"
    guidance: str = Field(default="None.", description="The FDE's approved learnings for this step, as text")


class DeepReviewResult(BaseModel):
    output: DeepReview
    total_tokens: int = 0


class ActionPointItem(BaseModel):
    id: str
    follow_up: str
    topic: str
    stated_due: str | None = Field(default=None, description="Only when the due date is not already YYYY-MM-DD")
    targets: list[str] = Field(default=[], description="Only when the account has several; the first is the default")


class ActionPointRequest(BaseModel):
    day: str = Field(description="The log's date, YYYY-MM-DD")
    weekday: str
    items: list[ActionPointItem]
    guidance: str = Field(default="None.", description="The FDE's approved learnings for this step, as text")


class ActionPointResult(BaseModel):
    output: ActionPointResolution
    total_tokens: int = 0


class ActionPointResolver(Protocol):
    def resolve(self, request: ActionPointRequest) -> ActionPointResult: ...


class TaskMatchCandidate(BaseModel):
    id: str
    content: str
    description: str = ""
    due_date: str | None = None


class TaskMatchItem(BaseModel):
    id: str
    follow_up: str
    topic: str
    account: str
    candidates: list[TaskMatchCandidate]


class TaskMatchRequest(BaseModel):
    day: str = Field(description="The log's date, YYYY-MM-DD")
    items: list[TaskMatchItem]
    guidance: str = Field(default="None.", description="The FDE's approved learnings for this step, as text")


class TaskMatchResult(BaseModel):
    output: TaskMatching
    total_tokens: int = 0


class TaskMatcher(Protocol):
    def match(self, request: TaskMatchRequest) -> TaskMatchResult: ...


class DeepReviewer(Protocol):
    def review(self, request: DeepReviewRequest) -> DeepReviewResult: ...


class TopicGrouper(Protocol):
    def group(self, request: GroupingRequest) -> GroupingResult: ...


class TopicSynthesizer(Protocol):
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...
