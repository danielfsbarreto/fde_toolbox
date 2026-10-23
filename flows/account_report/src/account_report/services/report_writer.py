"""Writes an account report from final daily logs: one section per account, then the title and overview.

Plain code reads the days, numbers the topics (T1, T2, ...), checks what the LLM returns (dates must be days with
activity, cited topics must exist) and turns cited topics into their evidence links. The follow-ups and their
Todoist status are code too. Only final days are read: drafts are not the record of what happened.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta

import httpx

from account_report.drafts import OverviewDraft, SectionDraft
from fde_core.domain.daily_log import DailyLog
from fde_core.domain.reports import (
    LANGUAGES,
    AccountSection,
    Development,
    OpenFollowUp,
    Report,
    ReportContent,
)
from fde_core.domain.topics import Topic
from fde_core.repositories.action_items import MongoActionItemRepository
from fde_core.repositories.daily_logs import MongoDailyLogRepository
from fde_core.repositories.reports import MongoReportRepository

log = logging.getLogger(__name__)
TODOIST_TASK_URL = "https://app.todoist.com/app/task/{}"

SectionWriter = Callable[[dict[str, str]], tuple[SectionDraft, int]]
OverviewWriter = Callable[[dict[str, str]], tuple[OverviewDraft, int]]


class ReportWriter:
    def __init__(
        self,
        *,
        reports: MongoReportRepository,
        daily_logs: MongoDailyLogRepository,
        action_items: MongoActionItemRepository,
        write_section: SectionWriter,
        write_overview: OverviewWriter,
        todoist_token: str | None,
        section_max_chars: int,
    ) -> None:
        self._reports = reports
        self._daily_logs = daily_logs
        self._items = action_items
        self._write_section = write_section
        self._write_overview = write_overview
        self._todoist_token = todoist_token
        self._max_chars = section_max_chars

    def run(self, report_id: str) -> str:
        report = self._reports.find(report_id)
        if report is None:
            raise ValueError(f"no report {report_id!r}: the review app creates it before starting the flow")
        self._reports.start(report.fde_id, report.id)
        try:
            content, tokens = self._write(report)
        except Exception as error:
            self._reports.fail(report.fde_id, report.id, f"{type(error).__name__}: {error}")
            raise
        self._reports.finish(report.fde_id, report.id, content, tokens)
        return f"# {content.title}\n\n{content.overview}\n"

    # Writing --------------------------------------------------------------------------------------------

    def _write(self, report: Report) -> tuple[ReportContent, int]:
        request = report.request
        days = [request.start + timedelta(days=n) for n in range((request.end - request.start).days + 1)]
        logs = [
            log for day in days if (log := self._daily_logs.get(report.fde_id, day)) and log.review_status == "final"
        ]
        covered = {log.date for log in logs}
        missing = [day for day in days if day.weekday() < 5 and day not in covered]
        period = f"{request.start.isoformat()} to {request.end.isoformat()}"
        language = LANGUAGES.get(request.language, "English")
        comment = request.comment.strip() or "none given: a factual report for the account's stakeholders"

        sections, tokens = [], 0
        for key in request.accounts:
            section, used = self._section(report, key, logs, period, language, comment)
            sections.append(section)
            tokens += used
        overview, used = self._write_overview(
            {
                "period": period,
                "language": language,
                "comment": comment,
                "sections": "\n\n".join(_section_text(section) for section in sections),
                "missing": ", ".join(day.isoformat() for day in missing) or "none",
            }
        )
        content = ReportContent(
            title=overview.title.strip(),
            overview=overview.overview.strip(),
            sections=sections,
            days_covered=sorted(covered),
            days_missing=missing,
        )
        return content, tokens + used

    def _section(
        self, report: Report, key: str, logs: list[DailyLog], period: str, language: str, comment: str
    ) -> tuple[AccountSection, int]:
        name = key
        refs: dict[str, tuple[date, Topic, list[str]]] = {}
        blocks: list[str] = []
        for daily in logs:
            digest = next((item for item in daily.accounts if item.account_key == key), None)
            if digest is None:
                continue
            name = digest.account_name
            if not digest.topics:
                continue
            links = _links(daily, key)
            lines = [f"## {daily.date.isoformat()} ({daily.date.strftime('%A')})", f"Headline: {digest.headline}"]
            if daily.comments.get(key):
                lines.append(f"The engineer's comment: {daily.comments[key]}")
            for topic in digest.topics:
                ref = f"T{len(refs) + 1}"
                refs[ref] = (daily.date, topic, [url for event_id in topic.evidence if (url := links.get(event_id))])
                lines.append(_topic_text(ref, topic))
            blocks.append("\n".join(lines))
        if not blocks:
            return AccountSection(account_key=key, account_name=name, summary="No reviewed activity in the period."), 0

        text = "\n\n".join(blocks)
        if len(text) > self._max_chars:
            text = text[: self._max_chars] + "\n(truncated: the period is longer than one call can read)"
        draft, tokens = self._write_section(
            {"account_name": name, "period": period, "language": language, "comment": comment, "days": text}
        )
        active = {day for day, _, _ in refs.values()}
        developments = []
        for item in draft.developments:
            cited = [ref for ref in dict.fromkeys(item.topics) if ref in refs]
            day = _date(item.date) or (refs[cited[0]][0] if cited else None)
            if day not in active or not item.text.strip():
                continue  # a date outside the account's days, or empty: not evidence-backed
            links = list(dict.fromkeys(url for ref in cited for url in refs[ref][2]))
            developments.append(Development(date=day, text=item.text.strip(), links=links))
        developments.sort(key=lambda item: item.date)
        return (
            AccountSection(
                account_key=key,
                account_name=name,
                summary=draft.summary.strip(),
                developments=developments,
                decisions=[text.strip() for text in draft.decisions if text.strip()],
                risks=[text.strip() for text in draft.risks if text.strip()],
                follow_ups=self._follow_ups(report.fde_id, [(day, topic) for day, topic, _ in refs.values()]),
                days_with_activity=sorted(active),
            ),
            tokens,
        )

    # Follow-ups and their Todoist status ------------------------------------------------------------------

    def _follow_ups(self, fde_id: str, topics: list[tuple[date, Topic]]) -> list[OpenFollowUp]:
        tasks: dict[str, str] = {}
        for day in {day for day, _ in topics}:
            for item in self._items.list_day(fde_id, day):
                for follow_up_id in item.follow_up_ids:
                    tasks[follow_up_id] = item.task_id
        statuses = self._todoist_statuses(set(tasks.values()))
        result = []
        for day, topic in topics:
            for follow_up in topic.follow_ups:
                task_id = tasks.get(follow_up.id)
                status = statuses.get(task_id, "unknown") if task_id else "not_sent"
                if status == "deleted":
                    continue  # removed in Todoist: no longer something to do
                result.append(
                    OpenFollowUp(
                        date=day,
                        what=follow_up.what,
                        owner=follow_up.owner,
                        due=follow_up.due_if_stated,
                        todoist=status,  # type: ignore[arg-type]
                        url=TODOIST_TASK_URL.format(task_id) if task_id else None,
                    )
                )
        return result

    def _todoist_statuses(self, task_ids: set[str]) -> dict[str, str]:
        """open, completed or deleted per task; unknown when Todoist is not configured or does not answer."""
        if not task_ids or not self._todoist_token:
            return {}
        statuses: dict[str, str] = {}
        headers = {"Authorization": f"Bearer {self._todoist_token}"}
        with httpx.Client(base_url="https://api.todoist.com/api/v1", headers=headers, timeout=20) as http:
            for task_id in task_ids:
                try:
                    response = http.get(f"/tasks/{task_id}")
                except httpx.HTTPError as error:
                    log.warning("Todoist status not read: %s", type(error).__name__)
                    continue
                if response.status_code == 404:
                    statuses[task_id] = "deleted"
                elif response.is_success:
                    statuses[task_id] = "completed" if response.json().get("checked") else "open"
        return statuses


def _links(daily: DailyLog, key: str) -> dict[str, str]:
    """Event id -> its link, from the day's activity for the account."""
    return {event_id: line.url for line in daily.activity.get(key, []) if line.url for event_id in line.event_ids}


def _topic_text(ref: str, topic: Topic) -> str:
    lines = [f"- {ref} [{topic.workstream}] {topic.title}", f"  What happened: {topic.what_happened}"]
    if topic.status_or_decision:
        lines.append(f"  Status or decision: {topic.status_or_decision}")
    if topic.risks:
        lines.append(f"  Risks: {'; '.join(topic.risks)}")
    if topic.people:
        lines.append(f"  People: {', '.join(topic.people)}")
    if topic.follow_ups:
        items = [f"{item.what}" + (f" (owner {item.owner})" if item.owner else "") for item in topic.follow_ups]
        lines.append(f"  Follow-ups: {'; '.join(items)}")
    return "\n".join(lines)


def _section_text(section: AccountSection) -> str:
    lines = [f"## {section.account_name}", section.summary]
    if section.decisions:
        lines.append("Decisions: " + " ".join(section.decisions))
    if section.risks:
        lines.append("Risks and open issues: " + " ".join(section.risks))
    return "\n".join(lines)


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
