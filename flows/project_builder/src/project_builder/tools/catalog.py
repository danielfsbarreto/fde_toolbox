"""The architect's lookup: what integrations exist for a need, from the installed catalogs."""

from __future__ import annotations

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from project_builder.services import integrations


class FindIntegrationsInput(BaseModel):
    need: str = Field(
        description="What the project needs to do, in a few words (e.g. 'send email', 'crm contacts'), or a "
        "crewai_tools class name (e.g. 'ExaSearchTool') for that tool's call arguments, settings and env vars"
    )


class FindIntegrations(BaseTool):
    name: str = "find_integrations"
    description: str = (
        "Find the open-source crewai_tools built-ins (the first choice) and the CrewAI AMP platform integrations (apps "
        "and their actions) that do something, by keywords; or one crewai_tools class in full, by its class name. Use "
        "it before choosing a tool, in this order: crewai_tools, then AMP integrations, then a custom tool only when "
        "nothing fits or the logic is the project's own."
    )
    args_schema: type[BaseModel] = FindIntegrationsInput

    def _run(self, need: str) -> str:
        return integrations.search(need)
