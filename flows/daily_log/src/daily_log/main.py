"""Orchestration shell. Every step calls one facade method and keeps only ids, dates and counts in state."""

import json
import sys
from typing import Literal

from crewai.flow import human_feedback
from crewai.flow.flow import Flow, listen, or_, router, start

from daily_log.bootstrap import REVIEW_OUTCOME_LLM, app
from fde_core.domain.run import RunState

REVIEW_MESSAGE = (
    "Review the day item by item in the review app (the link is below). Then reply with nothing to finalize it, or "
    "describe what the agent should look for or rework; you will see the day again."
)


class ActivityLogFlow(Flow[RunState]):
    """The daily activity log of one FDE, for one day."""

    @start()
    def run_daily(self):
        """Resolves the inputs into the day to run (and opens the run's record), then builds that day's log."""
        self.state.run = app().run_resolver.resolve(self.state)
        self.state.result = app().daily_log.run(self.state.run)

    @router(run_daily)
    def check_review(self) -> Literal["needs_review", "already_final"]:
        return "needs_review" if app().daily_review.needs_review(self.state.run) else "already_final"

    # One review pause with a single outcome, so crewai cannot misroute a reply. Item edits are made directly in
    # the review UI; the reply here is either "finalize" or requests for the agent (see facades/daily_review.py).
    @human_feedback(
        message=REVIEW_MESSAGE,
        emit=["day_reviewed"],
        llm=REVIEW_OUTCOME_LLM,
        default_outcome="day_reviewed",
        metadata={"stage": "day"},
    )
    @listen(or_("needs_review", "day_again"))
    def review_day(self) -> str:
        return app().daily_review.draft_day(self.state.run)

    @router("day_reviewed")
    def apply_day_review(self) -> Literal["day_again", "day_done"]:
        feedback = self.last_human_feedback
        if feedback is None:  # an empty reply would mean "finalize": never guess it
            raise RuntimeError("the review pause resumed without a reply")
        done = app().daily_review.apply_day_review(self.state.run, feedback.feedback)
        return "day_done" if done else "day_again"

    @listen("day_done")
    def finalize_log(self):
        app().daily_review.finalize(self.state.run)

    @listen(or_(finalize_log, "already_final"))
    def sync_action_points(self):
        self.state.action_points = app().action_points.sync(self.state.run)

    @listen(sync_action_points)
    def finish_run(self) -> str:
        """Returns the run's report (Markdown): the result shown on AMP and by /status."""
        return app().run_recorder.finish(
            self.state.run, self.state.result, self.usage_metrics, self.state.action_points
        )


def kickoff():
    """`uv run kickoff '{"date": "2026-10-13"}'` (from flows/daily_log); AMP passes inputs itself."""
    inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    ActivityLogFlow().kickoff(inputs=inputs)


def plot():
    ActivityLogFlow().plot()


if __name__ == "__main__":
    kickoff()
