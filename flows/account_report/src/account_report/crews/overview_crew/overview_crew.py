from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from account_report.drafts import OverviewDraft


@CrewBase
class OverviewCrew:
    """Writes a report's title and overview from its sections."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def report_editor(self) -> Agent:
        return Agent(config=self.agents_config["report_editor"], llm=LLM(model=self._llm), allow_delegation=False)  # type: ignore[index]

    @task
    def write_overview(self) -> Task:
        return Task(config=self.tasks_config["write_overview"], output_pydantic=OverviewDraft)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)
