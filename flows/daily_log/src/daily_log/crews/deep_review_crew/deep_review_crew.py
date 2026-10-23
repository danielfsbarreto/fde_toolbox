from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from daily_log.ports.llm import DeepReviewRequest, DeepReviewResult
from fde_core.domain.review_request import DeepReview


@CrewBase
class DeepReviewCrew:
    """Answers one review request: a deeper look at the day's events, or a rework of a section."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def deep_reviewer(self) -> Agent:
        return Agent(config=self.agents_config["deep_reviewer"], llm=LLM(model=self._llm), allow_delegation=False)  # type: ignore[index]

    @task
    def review_day(self) -> Task:
        return Task(config=self.tasks_config["review_day"], output_pydantic=DeepReview)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)


class CrewDeepReviewer:
    """DeepReviewer port backed by DeepReviewCrew."""

    def __init__(self, llm: str) -> None:
        self._llm = llm

    def review(self, request: DeepReviewRequest) -> DeepReviewResult:
        output = DeepReviewCrew(self._llm).crew().kickoff(inputs=request.model_dump())
        review = (
            output.pydantic
            if isinstance(output.pydantic, DeepReview)
            else DeepReview(notes=["The agent's answer could not be read; nothing was changed."])
        )
        return DeepReviewResult(output=review, total_tokens=output.token_usage.total_tokens)
