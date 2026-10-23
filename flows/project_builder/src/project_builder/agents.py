"""The specialist agents. Built fresh for every turn: nothing an agent holds outlives the turn it answers."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from crewai import LLM, Agent
from crewai.skills.loader import discover_skills
from crewai.skills.models import Skill

HERE = Path(__file__).parent
SKILLS_DIR = HERE / "skills"  # github.com/crewaiinc/skills, refreshed with bin/update_skills
CONFIG: dict[str, dict[str, str]] = yaml.safe_load((HERE / "config" / "agents.yaml").read_text())

# Each agent loads only the skills its job needs; it reads one in full when it decides it needs it
SKILLS: dict[str, tuple[str, ...]] = {
    "spec_writer": ("getting-started",),
    "architect": ("getting-started", "build-flow", "design-agent", "design-task", "connect-tools-and-mcp"),
    "engineer": (
        "check-crewai-api",
        "build-flow",
        "design-agent",
        "design-task",
        "connect-tools-and-mcp",
        "test-crewai-project",
    ),
}


# Every agent of the flow follows this: unsure means ask, never guess
CLARIFY = (
    " When you are unsure about anything that changes the work (a requirement, a choice between options, a missing "
    "detail), you do not guess or assume: you stop and ask the user, through the questions field of your answer, "
    "offering concrete options and recommending one. You continue once they answer. The user answers in a form "
    "built from those questions, so your message only says briefly why you ask: never repeat the questions in it "
    "or explain how to answer. Ask only about your own work: never whether to approve something or move to "
    "another stage (the user does that with the gate buttons), and never about a later stage's work."
)


def skills(*names: str) -> list[Skill]:
    found = {skill.name: skill for skill in discover_skills(SKILLS_DIR)}
    return [found[name] for name in names]


def spec_writer(model: str) -> Agent:
    config = CONFIG["spec_writer"]
    return Agent(
        **{**config, "backstory": config["backstory"].strip() + CLARIFY},
        llm=LLM(model=model, timeout=300),
        skills=skills(*SKILLS["spec_writer"]),
        allow_delegation=False,
        max_iter=10,
    )


def architect(model: str) -> Agent:
    from project_builder.tools.catalog import FindIntegrations

    config = CONFIG["architect"]
    tools: list = [FindIntegrations()]
    if os.environ.get("EXA_API_KEY"):  # web search, for what lies outside CrewAI: external APIs and libraries
        from project_builder.tools.web import WebSearch

        tools.append(WebSearch(max_usage_count=5))
    return Agent(
        **{**config, "backstory": config["backstory"].strip() + CLARIFY},
        llm=LLM(model=model, timeout=300),
        tools=tools,
        skills=skills(*SKILLS["architect"]),
        allow_delegation=False,
        max_iter=20,
    )


def engineer(model: str, tools: list) -> Agent:
    """The engineer, with the tools of one build's sandbox (sandbox_tools); read-only ones to answer a question."""
    config = CONFIG["engineer"]
    if os.environ.get("EXA_API_KEY"):
        from project_builder.tools.web import WebSearch

        tools = [*tools, WebSearch(max_usage_count=3)]
    return Agent(
        **{**config, "backstory": config["backstory"].strip() + CLARIFY},
        llm=LLM(model=model, timeout=300),
        tools=tools,
        skills=skills(*SKILLS["engineer"]),
        allow_delegation=False,
        max_iter=60,
        max_execution_time=1_800,  # well under AMP's ~1 h per run, with the checks and the smoke test after it
    )
