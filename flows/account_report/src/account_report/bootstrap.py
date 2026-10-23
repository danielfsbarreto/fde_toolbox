"""Composition root: the report writer with its storage, crews and Todoist access. Built once per process."""

from __future__ import annotations

import logging
from functools import cache

from dotenv import load_dotenv

from account_report.crews.overview_crew.overview_crew import OverviewCrew
from account_report.crews.section_crew.section_crew import SectionCrew
from account_report.drafts import OverviewDraft, SectionDraft
from account_report.services.report_writer import ReportWriter
from account_report.settings import Settings
from fde_core.repositories.action_items import MongoActionItemRepository
from fde_core.repositories.daily_logs import MongoDailyLogRepository
from fde_core.repositories.indexes import ensure_indexes
from fde_core.repositories.reports import MongoReportRepository


def _kickoff(crew_class: type, model_type: type, llm: str):
    """A crew as a function of its inputs: its structured output and the tokens it used."""

    def write(inputs: dict[str, str]):
        output = crew_class(llm).crew().kickoff(inputs=inputs)
        if not isinstance(output.pydantic, model_type):
            raise ValueError(f"{crew_class.__name__} returned no {model_type.__name__}")
        return output.pydantic, output.token_usage.total_tokens

    return write


@cache
def app() -> ReportWriter:
    load_dotenv(override=False)
    logging.getLogger("account_report").setLevel(logging.INFO)
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment (pydantic-settings)
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi

    db = MongoClient(
        settings.mongodb_connection_string.get_secret_value(),
        server_api=ServerApi("1"),
        tz_aware=True,
        appname="fde-activity-log-report",
    )[settings.mongodb_database]
    ensure_indexes(db)
    return ReportWriter(
        reports=MongoReportRepository(db),
        daily_logs=MongoDailyLogRepository(db),
        action_items=MongoActionItemRepository(db),
        write_section=_kickoff(SectionCrew, SectionDraft, settings.report_llm_model),
        write_overview=_kickoff(OverviewCrew, OverviewDraft, settings.report_llm_model),
        todoist_token=settings.todoist_api_key.get_secret_value() if settings.todoist_api_key else None,
        section_max_chars=settings.section_max_chars,
    )
