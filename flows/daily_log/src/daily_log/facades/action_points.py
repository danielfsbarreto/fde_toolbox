"""Sends a final day's action points to the FDE's task tracker. Never fails the run: problems end up in the summary.

Runs after the day is finalized, and again when a final day is run again, so a sync that failed (or a follow-up
not sent yet) is sent then. Each task is recorded by key, so nothing is sent twice; tasks are never edited or
deleted, since the FDE may have changed or completed them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from daily_log.ports.repositories import ActionItemRepository, DailyLogRepository, RunRepository
from daily_log.ports.tasks import TaskTracker
from daily_log.services.action_points import INBOX, ActionPointService, comment_on, describe
from daily_log.services.errors import describe_error
from daily_log.services.guidance import GuidanceService
from daily_log.services.profile import ProfileService
from fde_core.domain.action_points import ActionItemRecord, SyncSummary
from fde_core.domain.run import RunPlan


class ActionPointsFacade:
    def __init__(
        self,
        *,
        profiles: ProfileService,
        daily_logs: DailyLogRepository,
        items: ActionItemRepository,
        action_points: ActionPointService,
        tracker: TaskTracker | None,
        runs: RunRepository,
        guidance: GuidanceService,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._profiles = profiles
        self._daily_logs = daily_logs
        self._items = items
        self._action_points = action_points
        self._tracker = tracker
        self._runs = runs
        self._guidance = guidance
        self._clock = clock

    def sync(self, plan: RunPlan) -> SyncSummary:
        summary = self._sync(plan)
        self._runs.record_action_points(plan.run_id, plan.fde_id, summary)
        return summary

    def _sync(self, plan: RunPlan) -> SyncSummary:
        summary = SyncSummary(status="ok")
        try:
            profile = self._profiles.load(plan.fde_id)
            configured = profile.internal.todoist or any(account.todoist for account in profile.owned_accounts)
            if self._tracker is None or not configured:
                return SyncSummary(status="skipped", warnings=["Todoist not configured"])
            log = self._daily_logs.get(plan.fde_id, plan.day)
            if log is None or log.review_status != "final":
                return SyncSummary(status="skipped", warnings=["the day is not final"])
            points = self._action_points.extract(log, profile)
            known = self._items.known_keys(plan.fde_id, [point.key for point in points])
            summary.existing = len(known)
            for point in points:
                if point.key in known:
                    self._items.link_follow_ups(plan.fde_id, point.key, point.follow_up_ids)
            new = [point for point in points if point.key not in known]
            if not new:
                return summary
            guidance = self._guidance.load(profile)
            placements, warnings, tokens = self._action_points.place(
                new, profile, self._tracker.projects(), self._tracker.sections(), log.date, guidance
            )
            summary.warnings, summary.tokens = warnings, tokens
            # What is already open where each follow-up would land (made by hand or not): covered ones become comments
            open_tasks = {}
            for scope in dict.fromkeys(scope for placement in placements for scope in placement.scopes):
                try:
                    open_tasks[scope] = self._tracker.open_tasks(*scope)
                except Exception as error:
                    summary.warnings.append(f"open tasks of a Todoist target not read ({describe_error(error)})")
            matched, warnings, tokens = self._action_points.match(placements, open_tasks, log.date, guidance)
            summary.warnings += warnings
            summary.tokens += tokens
            for index, placement in enumerate(placements):
                existing = matched.get(index)
                if existing is not None:
                    comment_id = self._tracker.add_comment(existing.id, comment_on(placement))
                    inbox = placement.scopes[-1][0] if placement.scopes else None
                    record = dict(
                        title=existing.content,
                        task_id=existing.id,
                        target=INBOX if existing.project_id == inbox else placement.target,
                        action="commented",
                        comment_id=comment_id,
                    )
                    summary.commented += 1
                else:
                    task = describe(placement)
                    record = dict(title=task.content, task_id=self._tracker.create_task(task), target=placement.target)
                    summary.created += 1
                    summary.mine += placement.point.mine
                    summary.waiting += not placement.point.mine
                self._items.record(
                    ActionItemRecord(
                        fde_id=plan.fde_id,
                        date=log.date,
                        key=placement.point.key,
                        account_key=placement.point.account_key,
                        due_date=placement.due_date,
                        created_at=self._clock(),
                        follow_up_ids=placement.point.follow_up_ids,
                        **record,
                    )
                )
        except Exception as error:
            summary.error = describe_error(error)
            summary.status = "partial" if summary.created or summary.commented else "failed"
        return summary
