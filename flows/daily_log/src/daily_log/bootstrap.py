"""Composition root: the only place concrete classes are built and wired."""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from datetime import timedelta

from dotenv import load_dotenv

from daily_log.clients.amp_actions import AmpActionRunner
from daily_log.clients.mongo import connect
from daily_log.clients.todoist import TodoistClient
from daily_log.collectors.calendar import CalendarCollector
from daily_log.collectors.fathom import FathomCollector
from daily_log.collectors.github import GitHubCollector
from daily_log.collectors.gmail import GmailCollector
from daily_log.collectors.granola import GranolaCollector
from daily_log.collectors.linear import LinearCollector
from daily_log.collectors.slack import SlackCollector
from daily_log.collectors.teams import TeamsCollector
from daily_log.crews.action_point_crew.action_point_crew import CrewActionPointResolver
from daily_log.crews.deep_review_crew.deep_review_crew import CrewDeepReviewer
from daily_log.crews.task_match_crew.task_match_crew import CrewTaskMatcher
from daily_log.crews.topic_grouping_crew.topic_grouping_crew import CrewTopicGrouper
from daily_log.crews.topic_synthesis_crew.topic_synthesis_crew import CrewTopicSynthesizer
from daily_log.facades.action_points import ActionPointsFacade
from daily_log.facades.daily_log import DailyLogFacade
from daily_log.facades.daily_review import DailyReviewFacade
from daily_log.facades.run_recorder import RunRecorder
from daily_log.facades.run_resolver import RunResolver
from daily_log.ports.collectors import Collector
from daily_log.ports.integrations import ActionRunner
from daily_log.services.account_matching import AccountMatchingService
from daily_log.services.action_points import ActionPointService
from daily_log.services.collection import CollectionService
from daily_log.services.day_writer import DayWriter
from daily_log.services.deep_review import DeepReviewService
from daily_log.services.evidence_validation import EvidenceValidationService
from daily_log.services.guidance import GuidanceService
from daily_log.services.identity import IdentityService
from daily_log.services.log_rendering import LogRenderingService
from daily_log.services.meeting_merge import MeetingMergeService
from daily_log.services.normalization import NormalizationService
from daily_log.services.profile import ProfileService
from daily_log.services.redaction import RedactionService
from daily_log.services.reference_data import ReferenceDataService
from daily_log.services.relevance import RelevanceService
from daily_log.services.topic_grouping import TopicGroupingService
from daily_log.services.topic_synthesis import TopicSynthesisService
from daily_log.settings import DEFAULT_LLM, Settings
from daily_log.yaml_profiles import YamlFdeProfileRepository
from fde_core.domain.profile import FdeProfile
from fde_core.repositories.action_items import MongoActionItemRepository
from fde_core.repositories.collected_events import MongoCollectedEventRepository
from fde_core.repositories.daily_logs import MongoDailyLogRepository
from fde_core.repositories.day_overrides import MongoDayOverridesRepository
from fde_core.repositories.events import MongoEventRepository
from fde_core.repositories.indexes import ensure_indexes
from fde_core.repositories.learnings import MongoLearningRepository
from fde_core.repositories.reference_cache import MongoReferenceCacheRepository
from fde_core.repositories.review_requests import MongoReviewRequestRepository
from fde_core.repositories.runs import MongoRunRepository

# The review steps have a single outcome, so this model only confirms it. Fixed at import (decorator argument).
REVIEW_OUTCOME_LLM = DEFAULT_LLM


@dataclass(frozen=True)
class App:
    run_resolver: RunResolver
    daily_log: DailyLogFacade
    daily_review: DailyReviewFacade
    action_points: ActionPointsFacade
    run_recorder: RunRecorder


def _collectors_for(actions: ActionRunner, cache: MongoReferenceCacheRepository):
    def build(profile: FdeProfile) -> list[Collector]:
        return [
            GitHubCollector(actions),
            GmailCollector(actions),
            CalendarCollector(actions),
            SlackCollector(actions, cache),
            TeamsCollector(actions),
            FathomCollector(actions),
            GranolaCollector(actions),
            LinearCollector(actions),
        ]

    return build


@functools.cache
def app() -> App:
    load_dotenv(override=False)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("daily_log").setLevel(logging.INFO)
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment (pydantic-settings)

    db = connect(settings.mongodb_connection_string.get_secret_value(), settings.mongodb_database)
    ensure_indexes(db)
    events = MongoEventRepository(db)
    daily_logs = MongoDailyLogRepository(db)
    runs = MongoRunRepository(db)
    cache = MongoReferenceCacheRepository(db)
    overrides = MongoDayOverridesRepository(db)
    rendering = LogRenderingService()

    actions = AmpActionRunner(
        connection=settings.amp_connection,
        attempts=settings.amp_retry_attempts,
        backoff_s=settings.amp_retry_backoff_s,
    )
    profiles = ProfileService(YamlFdeProfileRepository())
    identity = IdentityService(actions, cache, settings.reference_cache_days)
    references = ReferenceDataService(actions, cache, settings.reference_cache_days)

    guidance = GuidanceService(MongoLearningRepository(db))  # the FDE's approved learnings

    daily_log = DailyLogFacade(
        profiles=profiles,
        identity=identity,
        references=references,
        collection=CollectionService(
            _collectors_for(actions, cache),
            MongoCollectedEventRepository(db),
            RedactionService(settings.text_max_chars, settings.meeting_text_max_chars),
            timeout_s=settings.collector_timeout_s,
            settle=timedelta(hours=settings.collection_settle_hours),
            force_recollect=settings.force_recollect,
        ),
        normalization=NormalizationService(),
        meeting_merge=MeetingMergeService(),
        matching=AccountMatchingService(),
        relevance=RelevanceService(),
        grouping=TopicGroupingService(CrewTopicGrouper(settings.llm_model), max_chars=settings.grouping_max_chars),
        synthesis=TopicSynthesisService(CrewTopicSynthesizer(settings.llm_model), EvidenceValidationService()),
        rendering=rendering,
        events=events,
        daily_logs=daily_logs,
        overrides=overrides,
        runs=runs,
        guidance=guidance,
        retention_days=settings.event_retention_days,
    )
    action_items = MongoActionItemRepository(db)
    action_point_service = ActionPointService(
        CrewActionPointResolver(settings.llm_model), CrewTaskMatcher(settings.llm_model)
    )
    return App(
        run_resolver=RunResolver(profiles, identity, runs),
        daily_log=daily_log,
        daily_review=DailyReviewFacade(
            writer=DayWriter(
                profiles=profiles,
                events=events,
                daily_logs=daily_logs,
                overrides=overrides,
                rendering=rendering,
                retention_days=settings.event_retention_days,
            ),
            profiles=profiles,
            deep_review=DeepReviewService(
                CrewDeepReviewer(settings.review_llm_model), max_chars=settings.grouping_max_chars
            ),
            rendering=rendering,
            events=events,
            requests=MongoReviewRequestRepository(db),
            runs=runs,
            guidance=guidance,
            review_ui_url=settings.review_ui_url,
        ),
        action_points=ActionPointsFacade(
            profiles=profiles,
            daily_logs=daily_logs,
            items=action_items,
            action_points=action_point_service,
            tracker=(
                TodoistClient(
                    settings.todoist_api_key.get_secret_value(),
                    attempts=settings.amp_retry_attempts,
                    backoff_s=settings.amp_retry_backoff_s,
                )
                if settings.todoist_api_key
                else None
            ),
            runs=runs,
            guidance=guidance,
        ),
        run_recorder=RunRecorder(runs, daily_logs),
    )
