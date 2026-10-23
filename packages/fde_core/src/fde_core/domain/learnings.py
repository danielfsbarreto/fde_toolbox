"""Learnings: short guidance the FDE approved, which every LLM step of a run follows.

A learning is one imperative sentence, scoped to one account or to everything (general), and tied to the steps it is
about. Only approved learnings reach the crews; proposed, denied and retired ones are kept for the review app (and,
later, so the assessment does not propose a denied one again).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from fde_core.domain.topics import new_id

GENERAL = "general"
MAX_GUIDANCE_CHARS = 3000  # per step: guidance stays a short list, never a second prompt

LearningStep = Literal["attribution", "topics", "follow_ups"]
LearningStatus = Literal["proposed", "approved", "denied", "retired"]
LEARNING_STEPS: tuple[LearningStep, ...] = ("attribution", "topics", "follow_ups")

# The LLM steps of the daily log, and what each one is about
GROUPING: set[LearningStep] = {"attribution", "topics"}  # which account a conversation belongs to, what a topic is
WRITING: set[LearningStep] = {"topics", "follow_ups"}  # how topics and their follow-ups are written
REVIEW: set[LearningStep] = set(LEARNING_STEPS)  # the deep review can change any of it
FOLLOW_UPS: set[LearningStep] = {"follow_ups"}  # Todoist: due dates, targets, matching open tasks


class Learning(BaseModel):
    id: str = Field(default_factory=new_id)
    fde_id: str
    scope: str = Field(default=GENERAL, description='"general", or the key of the account it is about')
    steps: list[LearningStep] = Field(default=[], description="The steps it applies to; empty means every step")
    text: str
    status: LearningStatus = "approved"
    source: Literal["manual", "assessment"] = "manual"
    evidence: list[str] = Field(default=[], description="For proposals: the days and corrections behind it")
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class Guidance(BaseModel):
    """A run's approved learnings, turned into the guidance block of one step's prompt."""

    learnings: list[Learning] = []
    account_names: dict[str, str] = {}

    def text(self, steps: set[LearningStep], accounts: Iterable[str] | None = None) -> str:
        """The learnings for these steps: the general ones, and those of the given accounts (all when None)."""
        wanted = None if accounts is None else set(accounts)
        chosen = [
            learning
            for learning in self.learnings
            if learning.status == "approved"
            and (not learning.steps or steps & set(learning.steps))
            and (learning.scope == GENERAL or wanted is None or learning.scope in wanted)
        ]
        chosen.sort(key=lambda learning: (learning.scope != GENERAL, learning.scope, learning.created_at))
        lines, size = [], 0
        for learning in chosen:
            label = "General" if learning.scope == GENERAL else self.account_names.get(learning.scope, learning.scope)
            line = f"- {label}: {' '.join(learning.text.split())}"
            if size + len(line) > MAX_GUIDANCE_CHARS:
                break
            lines.append(line)
            size += len(line) + 1
        return "\n".join(lines) or "None."
