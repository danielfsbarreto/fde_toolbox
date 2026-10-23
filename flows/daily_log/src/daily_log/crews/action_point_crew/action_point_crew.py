from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from daily_log.ports.llm import ActionPointItem, ActionPointRequest, ActionPointResult
from fde_core.domain.action_points import ActionPointResolution


@CrewBase
class ActionPointCrew:
    """Resolves stated relative due dates against the log's date and picks a target for multi-target accounts."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def action_point_planner(self) -> Agent:
        return Agent(
            config=self.agents_config["action_point_planner"], llm=LLM(model=self._llm), allow_delegation=False
        )  # type: ignore[index]

    @task
    def resolve_action_points(self) -> Task:
        return Task(config=self.tasks_config["resolve_action_points"], output_pydantic=ActionPointResolution)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)


def _line(item: ActionPointItem) -> str:
    parts = [f"- {item.id}: {item.follow_up}", f"topic: {item.topic}"]
    if item.stated_due:
        parts.append(f"due as stated: {item.stated_due}")
    if item.targets:
        parts.append(f"targets: {'; '.join(item.targets)}")
    return " | ".join(parts)


class CrewActionPointResolver:
    """ActionPointResolver port backed by ActionPointCrew."""

    def __init__(self, llm: str) -> None:
        self._llm = llm

    def resolve(self, request: ActionPointRequest) -> ActionPointResult:
        output = (
            ActionPointCrew(self._llm)
            .crew()
            .kickoff(
                inputs={
                    "day": request.day,
                    "weekday": request.weekday,
                    "items": "\n".join(_line(item) for item in request.items),
                    "guidance": request.guidance,
                }
            )
        )
        resolution = output.pydantic if isinstance(output.pydantic, ActionPointResolution) else ActionPointResolution()
        return ActionPointResult(output=resolution, total_tokens=output.token_usage.total_tokens)
