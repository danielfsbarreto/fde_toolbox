"""What the LLM calls return. Code validates them and turns them into fde_core records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fde_core.domain.builds import Design, Question, Spec, Stage

Intent = Literal["DRAFT", "ASK", "APPROVE"]


class RouteDecision(BaseModel):
    intent: Intent = Field(
        description=(
            "DRAFT: write or change the current stage's artifact (new requirements, corrections, answers to its "
            "questions). ASK: a question or discussion that changes nothing. APPROVE: the user accepts the version "
            "waiting at the gate."
        )
    )
    stage: Stage | None = Field(
        default=None,
        description=(
            "For DRAFT: the stage the message changes. What the project does (requirements, inputs, outputs, "
            "behavior) belongs to the spec, even after it was approved; how it is built (shape, agents, tasks, "
            "tools, integrations) belongs to the design. Null means the current stage."
        ),
    )
    reasoning: str = Field(description="Why, in one sentence")


QUESTIONS = Field(
    default=[],
    description=(
        "When you are unsure about something that changes the work, do not guess: ask here (1 to 5 questions, each "
        "with 2 to 4 concrete options and the one you recommend) and do no work this turn. The user's answers "
        "come back to you. Empty when you are sure."
    ),
)


class Clarifiable(BaseModel):
    """Every agent's result: the work, or the questions it needs answered first."""

    questions: list[Question] = QUESTIONS


class SpecTurn(Clarifiable):
    reply: str = Field(
        description="Your message to the user, in Markdown: what you did; or, when you ask, one or two sentences on why"
    )
    spec: Spec | None = Field(
        default=None,
        description="The whole spec as it should now be, when you write or change it; null when you ask questions",
    )
    summary: str = Field(default="", description="What changed in this version, in one line")
    changes: list[str] = Field(default=[], description="The changes, one short line each (empty for the first version)")


class AskTurn(Clarifiable):
    reply: str = Field(description="Your answer to the user, in Markdown; or why you ask, when you ask questions")


class DesignTurn(Clarifiable):
    reply: str = Field(
        description="Your message to the user, in Markdown: what you designed and why; or, when you ask, why"
    )
    design: Design | None = Field(
        default=None, description="The whole design as it should now be; null when you ask questions"
    )
    summary: str = Field(default="", description="What changed in this version, in one line")
    changes: list[str] = Field(default=[], description="The changes, one short line each (empty for the first version)")


class SmokeInput(BaseModel):
    """One value the smoke test needs from the user: a kickoff input, a trigger's sample payload, or a test target."""

    name: str = Field(description="The kickoff input's name (e.g. topic); crewai_trigger_payload for a trigger's JSON")
    prompt: str = Field(description="The question, e.g. 'Topic to search news for?'")
    why: str = Field(default="", description="One line, when it is not obvious")
    suggestions: list[str] = Field(default=[], description="1 to 3 realistic values, the recommended one first")
    optional: bool = Field(default=False, description="True when the project has a default for it (e.g. today's date)")


class CodeTurn(Clarifiable):
    reply: str = Field(description="Your message to the user, in Markdown: what you built (or fixed), briefly")
    summary: str = Field(default="", description="What changed in this version, in one line")
    changes: list[str] = Field(default=[], description="The changes, one short line each (empty for the first version)")
    smoke_inputs: list[SmokeInput] = Field(
        default=[],
        description=(
            "What the smoke test needs from the user to run the project for real: every kickoff input (with "
            "realistic suggestions from the spec's examples), a sample payload when a trigger starts it, and a test "
            "target for any tool that writes to the outside world (e.g. which Slack channel)"
        ),
    )
    unattended: bool = Field(
        default=True, description="False when the project can't run unattended here (e.g. it needs a human step)"
    )
