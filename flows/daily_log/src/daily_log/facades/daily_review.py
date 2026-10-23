"""The daily review: one pause of the flow, answered from the review UI (or by email / in the terminal).

Item-level changes are made directly in the review UI and never pass through here. The pause is answered with
either "finalize" (the day is final; Todoist follows) or agent requests (a deeper look at what was
already fetched, something the FDE thinks was missed, a rework of an account or a topic). After requests the
day is shown again.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from daily_log.ports.repositories import EventRepository, ReviewRequestRepository, RunRepository
from daily_log.services.day_writer import DayWriter
from daily_log.services.deep_review import DeepReviewService
from daily_log.services.errors import describe_error
from daily_log.services.guidance import GuidanceService
from daily_log.services.log_rendering import LogRenderingService
from daily_log.services.profile import ProfileService
from fde_core.domain.review_request import ReviewFeedback, ReviewRequest
from fde_core.domain.run import RunPlan
from fde_core.domain.window import DayWindow

DEEP_REVIEW_CREW = "deep_review_crew"


class DailyReviewFacade:
    def __init__(
        self,
        *,
        writer: DayWriter,
        profiles: ProfileService,
        deep_review: DeepReviewService,
        guidance: GuidanceService,
        rendering: LogRenderingService,
        events: EventRepository,
        requests: ReviewRequestRepository,
        runs: RunRepository,
        review_ui_url: str | None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._writer = writer
        self._profiles = profiles
        self._deep_review = deep_review
        self._guidance = guidance
        self._rendering = rendering
        self._events = events
        self._requests = requests
        self._runs = runs
        self._review_ui_url = review_ui_url.rstrip("/") if review_ui_url else None
        self._clock = clock

    def needs_review(self, plan: RunPlan) -> bool:
        try:
            return self._writer.load(plan.fde_id, plan.day).review_status != "final"
        except LookupError:
            return True

    def draft_day(self, plan: RunPlan) -> str:
        """Opens a review round and returns what AMP shows (dashboard and email): a summary and the UI link."""
        day = plan.day
        log = self._writer.load(plan.fde_id, day)
        if log.review_status == "working":  # the agent finished (or failed): the day is editable again
            log.review_status = "draft"
        # Bring in what the review app saved (additions, comments) and refresh activity and the report
        log = self._writer.sync_manual(log)
        request = ReviewRequest(
            id=uuid4().hex,
            fde_id=plan.fde_id,
            date=day,
            run_id=plan.run_id,
            round=self._requests.next_round(plan.fde_id, day),
            created_at=self._clock(),
        )
        self._requests.create(request)
        link = f"{self._review_ui_url}/day/{day.isoformat()}" if self._review_ui_url else None
        return self._rendering.render_review_message(log, self._profiles.load(plan.fde_id).name, request.round, link)

    def apply_day_review(self, plan: RunPlan, reply: str) -> bool:
        """Apply the answer to the pause. Returns True when the day is to be finalized."""
        try:
            return self._apply(plan, reply)
        except Exception as error:
            self._runs.fail(plan.run_id, plan.fde_id, describe_error(error))
            log = self._writer.load(plan.fde_id, plan.day)
            if log.review_status == "working":
                log.review_status = "draft"
                self._writer.save(log)
            raise

    def finalize(self, plan: RunPlan) -> None:
        log = self._writer.load(plan.fde_id, plan.day)
        self._writer.sync_manual(log, final=True)

    def _apply(self, plan: RunPlan, reply: str) -> bool:
        day = plan.day
        waiting = self._requests.waiting_for_run(plan.run_id)
        if waiting:  # answered outside the UI (AMP dashboard, email, terminal)
            self._requests.mark_answered(waiting.id, "flow")
        feedback = ReviewFeedback.parse(reply)
        overrides = self._writer.overrides(plan.fde_id, day)
        overrides.record(reply.strip() if feedback.action == "requests" else "finalize")
        self._writer.save_overrides(overrides)
        if feedback.action == "finalize":
            return True

        log = self._writer.load(plan.fde_id, day)
        log.review_status = "working"
        log = self._writer.save(log)
        profile = self._profiles.load(plan.fde_id)
        window = DayWindow.for_day(day, profile.timezone)
        events = [
            event.model_copy(update={"ts_local": window.to_local(event.ts_utc)})
            for event in self._events.list_day(plan.fde_id, day)
            if event.source != "manual"
        ]
        guidance = self._guidance.load(profile)
        topics, notes, tokens = self._deep_review.review(feedback.requests, log, events, profile, guidance)
        log = self._writer.load(plan.fde_id, day)
        log.usage.add_crew(DEEP_REVIEW_CREW, tokens)
        self._writer.replace_topics(log, topics, notes=notes, status="draft")
        return False
