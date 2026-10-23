"""Closes a run: final status and token usage in `runs` (and the DailyLog), and the report the run returns.

The returned Markdown is the flow's result, shown on the AMP execution page and by /status. It holds what the
DailyLog report holds (titles, links, counts), never message bodies.
"""

from __future__ import annotations

from typing import Any

from daily_log.ports.repositories import DailyLogRepository, RunRepository
from fde_core.domain.action_points import SyncSummary
from fde_core.domain.daily_log import DailyLog, Usage
from fde_core.domain.run import DayResult, RunPlan


def _usage(metrics: Any) -> Usage:
    return Usage(
        prompt_tokens=getattr(metrics, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(metrics, "completion_tokens", 0) or 0,
        cached_prompt_tokens=getattr(metrics, "cached_prompt_tokens", 0) or 0,
        total_tokens=getattr(metrics, "total_tokens", 0) or 0,
        successful_requests=getattr(metrics, "successful_requests", 0) or 0,
    )


class RunRecorder:
    def __init__(self, runs: RunRepository, daily_logs: DailyLogRepository) -> None:
        self._runs = runs
        self._daily_logs = daily_logs

    def finish(
        self,
        plan: RunPlan,
        result: DayResult | None,
        usage_metrics: Any,
        action_points: SyncSummary | None = None,
    ) -> str:
        usage = _usage(usage_metrics)
        self._runs.finish(plan.run_id, plan.fde_id, result.status if result else "failed", usage)
        log = self._daily_logs.get(plan.fde_id, plan.day)
        if log is not None and log.run_id == plan.run_id:  # this run built the log: keep its usage on it
            usage.crew_totals = log.usage.crew_totals
            self._daily_logs.set_usage(plan.fde_id, plan.day, usage)
        report = self._report(plan, result, log)
        notes = [action_points.line()] if action_points else []
        return "\n\n".join([report.rstrip(), *notes]) + "\n" if notes else report

    @staticmethod
    def _report(plan: RunPlan, result: DayResult | None, log: DailyLog | None) -> str:
        if log is not None:
            return log.markdown
        errors = [result.error] if result and result.error else []
        return "\n".join([f"# Daily log {plan.day}: {result.status if result else 'failed'}", "", *errors])
