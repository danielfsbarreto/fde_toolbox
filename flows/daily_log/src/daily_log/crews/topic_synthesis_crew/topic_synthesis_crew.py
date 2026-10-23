from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from daily_log.crews.prompting import LANGUAGES, format_events, format_groups
from daily_log.ports.llm import SynthesisResult
from fde_core.domain.topics import SynthesisOutput, SynthesisRequest


@CrewBase
class TopicSynthesisCrew:
    """Writes each topic of one account, and the account's headline, from the cited events."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm: str) -> None:
        self._llm = llm

    @agent
    def topic_writer(self) -> Agent:
        return Agent(config=self.agents_config["topic_writer"], llm=LLM(model=self._llm), allow_delegation=False)  # type: ignore[index]

    @task
    def write_topics(self) -> Task:
        return Task(config=self.tasks_config["write_topics"], output_pydantic=SynthesisOutput)  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)


class CrewTopicSynthesizer:
    """TopicSynthesizer port backed by TopicSynthesisCrew."""

    def __init__(self, llm: str) -> None:
        self._llm = llm

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        feedback = f"Your previous answer failed validation: {request.feedback}. Fix it." if request.feedback else ""
        output = (
            TopicSynthesisCrew(self._llm)
            .crew()
            .kickoff(
                inputs={
                    "account_key": request.account_key,
                    "account_name": request.account_name,
                    "account_description": f"Engagement: {request.account_description}."
                    if request.account_description
                    else "",
                    "fde_name": request.fde_name,
                    "output_language": LANGUAGES.get(request.output_language, request.output_language),
                    "groups": format_groups(request.groups),
                    "events": format_events(request.events),
                    "feedback": feedback,
                    "guidance": request.guidance,
                }
            )
        )
        written = (
            output.pydantic if isinstance(output.pydantic, SynthesisOutput) else SynthesisOutput(headline="", topics=[])
        )
        return SynthesisResult(output=written, total_tokens=output.token_usage.total_tokens)
