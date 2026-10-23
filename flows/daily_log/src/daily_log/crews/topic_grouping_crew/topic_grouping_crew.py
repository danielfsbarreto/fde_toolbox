from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from daily_log.crews.prompting import LANGUAGES, format_accounts, format_clusters
from daily_log.ports.llm import GroupingResult
from fde_core.domain.topics import GroupingOutput, GroupingRequest


@CrewBase
class TopicGroupingCrew:
    """Groups the whole day's conversation clusters into topics, each under the account it is about."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def topic_grouper(self) -> Agent:
        return Agent(config=self.agents_config["topic_grouper"], llm=LLM(model=self._llm), allow_delegation=False)  # type: ignore[index]

    @task
    def group_topics(self) -> Task:
        return Task(config=self.tasks_config["group_topics"], output_pydantic=GroupingOutput)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)


class CrewTopicGrouper:
    """TopicGrouper port backed by TopicGroupingCrew."""

    def __init__(self, llm: str) -> None:
        self._llm = llm

    def group(self, request: GroupingRequest) -> GroupingResult:
        output = (
            TopicGroupingCrew(self._llm)
            .crew()
            .kickoff(
                inputs={
                    "accounts": format_accounts(request.accounts),
                    "clusters": format_clusters(request.clusters),
                    "guidance": request.guidance,
                    "output_language": LANGUAGES.get(request.output_language, request.output_language),
                }
            )
        )
        grouped = output.pydantic if isinstance(output.pydantic, GroupingOutput) else GroupingOutput(topics=[])
        return GroupingResult(output=grouped, total_tokens=output.token_usage.total_tokens)
