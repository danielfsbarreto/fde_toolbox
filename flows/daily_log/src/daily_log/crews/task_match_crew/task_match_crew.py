from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from daily_log.ports.llm import TaskMatchItem, TaskMatchRequest, TaskMatchResult
from fde_core.domain.action_points import TaskMatching

DESCRIPTION_CHARS = 200


@CrewBase
class TaskMatchCrew:
    """Decides, per follow-up, whether an open Todoist task already covers it (comment) or not (new task)."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def task_matcher(self) -> Agent:
        return Agent(config=self.agents_config["task_matcher"], llm=LLM(model=self._llm), allow_delegation=False)  # type: ignore[index]

    @task
    def match_tasks(self) -> Task:
        return Task(config=self.tasks_config["match_tasks"], output_pydantic=TaskMatching)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)


def _block(item: TaskMatchItem) -> str:
    lines = [f"- {item.id}: {item.follow_up}", f"  account: {item.account} | topic: {item.topic}", "  candidates:"]
    for candidate in item.candidates:
        text = candidate.content
        if candidate.description:
            text += f" ({candidate.description[:DESCRIPTION_CHARS].replace(chr(10), ' ')})"
        due = f" | due {candidate.due_date}" if candidate.due_date else ""
        lines.append(f"    - {candidate.id}: {text}{due}")
    return "\n".join(lines)


class CrewTaskMatcher:
    """TaskMatcher port backed by TaskMatchCrew."""

    def __init__(self, llm: str) -> None:
        self._llm = llm

    def match(self, request: TaskMatchRequest) -> TaskMatchResult:
        output = (
            TaskMatchCrew(self._llm)
            .crew()
            .kickoff(
                inputs={
                    "day": request.day,
                    "items": "\n".join(_block(item) for item in request.items),
                    "guidance": request.guidance,
                }
            )
        )
        matching = output.pydantic if isinstance(output.pydantic, TaskMatching) else TaskMatching()
        return TaskMatchResult(output=matching, total_tokens=output.token_usage.total_tokens)
