"""The review app: API, AMP webhook, GitHub sign-in, and the built frontend.

Run: `uv run uvicorn review_api.app:app --proxy-headers --forwarded-allow-ips='*'` from ui/server (bin/start does
it, behind ngrok; on Heroku the Docker image does). Every /api route needs a signed-in session, except the AMP
webhook, which is checked by its signature instead.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from review_api import webhook
from review_api.auth import current_user
from review_api.auth import router as auth_router
from review_api.builds import router as builds_router
from review_api.callbacks import CallbackError
from review_api.contract import INTERNAL, MANUAL, UNASSIGNED
from review_api.edits import EditError, FinalError, LockedError, accounts
from review_api.report_render import ASSETS, report_html, report_pdf
from review_api.store import StaleError
from review_api.wiring import server

log = logging.getLogger(__name__)

app = FastAPI(title="FDE review", docs_url=None, redoc_url=None, openapi_url=None)
_settings = server().settings
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret.get_secret_value(),
    max_age=_settings.session_days * 86400,
    same_site="lax",
    https_only=True,
)
app.include_router(auth_router)
app.include_router(builds_router)
Signed = Depends(current_user)


@app.exception_handler(EditError)
def _edit_error(_: Request, error: EditError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(StaleError)
def _stale(_: Request, __: StaleError) -> JSONResponse:
    return JSONResponse({"detail": "The day changed meanwhile; it was reloaded."}, status_code=409)


@app.exception_handler(LockedError)
def _locked(_: Request, __: LockedError) -> JSONResponse:
    return JSONResponse({"detail": "The agent is working on this day."}, status_code=423)


@app.exception_handler(FinalError)
def _final(_: Request, __: FinalError) -> JSONResponse:
    return JSONResponse(
        {"detail": "This day is final and cannot change. To redo it, delete it in Settings and run it again."},
        status_code=409,
    )


@app.exception_handler(LookupError)
def _missing(_: Request, error: LookupError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=404)


# Views -----------------------------------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _review(request: dict[str, Any] | None) -> dict[str, Any] | None:
    if not request:
        return None
    return {
        "id": request["id"],
        "round": request.get("round", 1),
        "status": request.get("status", "waiting"),
        "created_at": _iso(request.get("created_at")),
        "can_answer": request.get("status") == "waiting" and bool(request.get("callback_ciphertext")),
    }


def _task(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """A follow-up's Todoist task, as the app links it."""
    if not item:
        return None
    due = item.get("due_date")
    return {
        "url": f"https://app.todoist.com/app/task/{item['task_id']}",
        "target": item.get("target"),
        "due_date": due.date().isoformat() if hasattr(due, "date") else (str(due) if due else None),
        # commented: an open task already covered the follow-up, so it was added there as a comment
        "action": item.get("action") or "created",
        "title": item.get("title"),
    }


def _topic(
    topic: dict[str, Any],
    links: dict[str, str],
    removed_follow_ups: list[dict] = (),
    tasks: dict[str, dict] | None = None,
) -> dict[str, Any]:
    keys = (
        "id",
        "reviewed",
        "agent_added",
        "title",
        "workstream",
        "what_happened",
        "status_or_decision",
        "risks",
        "my_involvement",
        "follow_ups",
        "people",
        "confidence",
        "inferred",
        "account_key",
        "attribution",
        "attribution_reason",
    )
    view = {key: topic.get(key) for key in keys}
    view["reviewed"] = bool(view["reviewed"])
    view["agent_added"] = bool(view["agent_added"])
    view["risks"] = view["risks"] or []
    view["people"] = view["people"] or []
    view["follow_ups"] = [
        {
            **{key: item.get(key) for key in ("id", "reviewed", "what", "owner", "due_if_stated")},
            "todoist": _task((tasks or {}).get(item.get("id"))),
        }
        for item in topic.get("follow_ups") or []
    ]
    view["removed_follow_ups"] = [
        {key: entry["follow_up"].get(key) for key in ("id", "what", "owner", "due_if_stated")}
        for entry in removed_follow_ups
        if entry.get("topic_id") == topic.get("id")
    ]
    view["links"] = list(dict.fromkeys(url for event_id in topic.get("evidence") or [] if (url := links.get(event_id))))
    return view


def _day(day: str) -> dict[str, Any]:
    s = server()
    log_ = s.store.day(day)
    overrides = s.store.overrides(day)
    comments = overrides.get("comments") or {}
    entries = overrides.get("manual_entries") or []
    removed_follow_ups = log_.get("removed_follow_ups") or []
    tasks = s.store.tasks(day)
    links = {
        event_id: line["url"]
        for lines in (log_.get("activity") or {}).values()
        for line in lines
        if line.get("url")
        for event_id in line.get("event_ids") or []
    }
    counts = {
        key: sum(len(line.get("event_ids") or []) for line in lines)
        for key, lines in (log_.get("activity") or {}).items()
    }

    def section(key: str, name: str, headline: str, topics: list[dict[str, Any]], quiet: bool) -> dict[str, Any]:
        own = [
            _topic(topic, links, removed_follow_ups, tasks) for topic in topics if topic.get("attribution") != MANUAL
        ]
        mine = [entry for entry in entries if entry.get("account_key") == key]
        return {
            "key": key,
            "name": name,
            "headline": headline,
            "comment": comments.get(key, ""),
            "quiet": quiet and not own and not mine,
            "events": counts.get(key, 0),
            "topics": own,
            "entries": mine,
        }

    sections = [
        section(
            digest["account_key"],
            digest["account_name"],
            digest.get("headline", ""),
            digest.get("topics") or [],
            bool(digest.get("quiet")),
        )
        for digest in log_.get("accounts") or []
    ]
    sections.append(section(INTERNAL, "Internal", "", log_.get("internal") or [], True))
    sections.append(section(UNASSIGNED, "Unassigned", "", log_.get("unassigned") or [], True))
    return {
        "date": day,
        "status": log_.get("review_status", "draft"),
        "version": int(log_.get("version", 0)),
        "reviewed_at": _iso(log_.get("reviewed_at")),
        "sections": sections,
        "removed": [_topic(topic, links) for topic in log_.get("removed") or []],
        "manual_entries": entries,
        "agent_notes": log_.get("unapplied_feedback") or [],
        "accounts": accounts(log_),
        "review": _review(s.store.latest_review(day)),
    }


# Reading ----------------------------------------------------------------------------------------------------


@app.get("/api/env")
def environment() -> dict:
    """Which copy of the app this is, for the colored bar on top. Local (bin/start, behind ngrok) is dev. No sign-in."""
    return {"env": "dev" if server().settings.ngrok_domain else "production"}


@app.get("/api/days")
def days(_: dict = Signed) -> dict[str, Any]:
    s = server()
    waiting = {str(request["date"]): request for request in s.store.waiting_reviews()}
    return {
        "fde_id": s.settings.fde_id,
        "days": [
            {
                "date": str(row["date"]),
                "status": row.get("review_status", "draft"),
                "reviewed_at": _iso(row.get("reviewed_at")),
                "review": _review(waiting.get(str(row["date"]))),
                **_progress(row),
            }
            for row in s.store.days()
        ],
    }


def _progress(log: dict[str, Any]) -> dict[str, int]:
    """Topics to review and how many are, counted as the day page counts them (additions are not reviewed)."""
    topics = [topic for digest in log.get("accounts") or [] for topic in digest.get("topics") or []]
    topics += (log.get("internal") or []) + (log.get("unassigned") or [])
    reviewable = [topic for topic in topics if topic.get("attribution") != MANUAL]
    return {"topics": len(reviewable), "reviewed": sum(1 for topic in reviewable if topic.get("reviewed"))}


@app.get("/api/days/{day}")
def get_day(day: str, _: dict = Signed) -> dict[str, Any]:
    return _day(day)


# Direct edits -------------------------------------------------------------------------------------------------


class Versioned(BaseModel):
    version: int


class Patch(Versioned):
    model_config = {"extra": "allow"}

    def changes(self) -> dict[str, Any]:
        return {key: value for key, value in self.model_dump().items() if key != "version"}


class MoveTopic(Versioned):
    account_key: str


class MoveFollowUp(Versioned):
    to_topic_id: str


class TextEdit(Versioned):
    text: str = ""


class EntryEdit(Versioned):
    entry: dict[str, Any]


def _edits():
    return server().edits


@app.patch("/api/days/{day}/topics/{topic_id}")
def edit_topic(day: str, topic_id: str, body: Patch, _: dict = Signed) -> dict:
    _edits().update_topic(day, body.version, topic_id, body.changes())
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/move")
def move_topic(day: str, topic_id: str, body: MoveTopic, _: dict = Signed) -> dict:
    _edits().move_topic(day, body.version, topic_id, body.account_key)
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/remove")
def remove_topic(day: str, topic_id: str, body: Versioned, _: dict = Signed) -> dict:
    _edits().remove_topic(day, body.version, topic_id)
    return _day(day)


class DeleteDay(BaseModel):
    confirm: str = Field(description="The date again, so a stray request cannot delete a day")


@app.delete("/api/days/{day}")
def delete_day(day: str, body: DeleteDay, _: dict = Signed) -> dict:
    """The whole day, so it can be run again from scratch (Todoist's sync records stay; see Store.delete_day)."""
    if body.confirm != day:
        raise HTTPException(status_code=400, detail="confirm with the date of the day to delete")
    if server().store.day(day).get("review_status") == "working":
        raise LockedError("the agent is working on this day")
    deleted = server().store.delete_day(day)
    log.warning("Deleted day %s: %s", day, deleted)
    return {"deleted": deleted}


@app.post("/api/days/{day}/topics/{topic_id}/restore")
def restore_topic(day: str, topic_id: str, body: Versioned, _: dict = Signed) -> dict:
    _edits().restore_topic(day, body.version, topic_id)
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/follow-ups")
def add_follow_up(day: str, topic_id: str, body: Patch, _: dict = Signed) -> dict:
    _edits().add_follow_up(day, body.version, topic_id, body.changes())
    return _day(day)


@app.patch("/api/days/{day}/topics/{topic_id}/follow-ups/{follow_up_id}")
def edit_follow_up(day: str, topic_id: str, follow_up_id: str, body: Patch, _: dict = Signed) -> dict:
    _edits().update_follow_up(day, body.version, topic_id, follow_up_id, body.changes())
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/follow-ups/{follow_up_id}/remove")
def remove_follow_up(day: str, topic_id: str, follow_up_id: str, body: Versioned, _: dict = Signed) -> dict:
    _edits().remove_follow_up(day, body.version, topic_id, follow_up_id)
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/follow-ups/{follow_up_id}/restore")
def restore_follow_up(day: str, topic_id: str, follow_up_id: str, body: Versioned, _: dict = Signed) -> dict:
    _edits().restore_follow_up(day, body.version, topic_id, follow_up_id)
    return _day(day)


@app.post("/api/days/{day}/topics/{topic_id}/follow-ups/{follow_up_id}/move")
def move_follow_up(day: str, topic_id: str, follow_up_id: str, body: MoveFollowUp, _: dict = Signed) -> dict:
    _edits().move_follow_up(day, body.version, topic_id, follow_up_id, body.to_topic_id)
    return _day(day)


@app.put("/api/days/{day}/headlines/{account_key}")
def set_headline(day: str, account_key: str, body: TextEdit, _: dict = Signed) -> dict:
    _edits().set_headline(day, body.version, account_key, body.text)
    return _day(day)


@app.put("/api/days/{day}/comments/{account_key}")
def set_comment(day: str, account_key: str, body: TextEdit, _: dict = Signed) -> dict:
    _edits().set_comment(day, body.version, account_key, body.text)
    return _day(day)


@app.post("/api/days/{day}/entries")
def add_entry(day: str, body: EntryEdit, _: dict = Signed) -> dict:
    _edits().add_entry(day, body.version, body.entry)
    return _day(day)


@app.put("/api/days/{day}/entries/{entry_id}")
def edit_entry(day: str, entry_id: str, body: EntryEdit, _: dict = Signed) -> dict:
    _edits().update_entry(day, body.version, entry_id, body.entry)
    return _day(day)


@app.post("/api/days/{day}/entries/{entry_id}/remove")
def remove_entry(day: str, entry_id: str, body: Versioned, _: dict = Signed) -> dict:
    _edits().remove_entry(day, body.version, entry_id)
    return _day(day)


# Answering the flow's pause (the reply format the flow parses: kind "fde-review") ------------------------------


class Scope(BaseModel):
    kind: Literal["day", "account", "topic"] = "day"
    key: str | None = None


class AgentRequest(BaseModel):
    scope: Scope = Scope()
    instruction: str = Field(min_length=1)


class Requests(BaseModel):
    requests: list[AgentRequest]


def _answer(review_id: str, action: str, requests: list[AgentRequest]) -> dict:
    s = server()
    request = s.store.review(review_id)
    if request is None:
        raise HTTPException(status_code=404, detail="no such review")
    if request.get("status") != "waiting":
        raise HTTPException(status_code=409, detail="this review was already answered")
    if not request.get("callback_ciphertext"):
        raise HTTPException(
            status_code=409,
            detail="AMP's notification for this review never reached the app; answer in AMP or by email",
        )
    feedback = {
        "kind": "fde-review",
        "version": 1,
        "action": action,
        "requests": [item.model_dump() for item in requests],
    }
    try:
        s.vault.answer(request["callback_ciphertext"], json.dumps(feedback))
    except CallbackError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    s.store.mark_answered(review_id)
    day = str(request["date"])
    if action == "requests":
        s.edits.mark_working(day)
    return _day(day)


@app.post("/api/reviews/{review_id}/requests")
def send_requests(review_id: str, body: Requests, _: dict = Signed) -> dict:
    requests = [item for item in body.requests if item.instruction.strip()]
    if not requests:
        raise HTTPException(status_code=400, detail="write at least one request")
    return _answer(review_id, "requests", requests)


@app.post("/api/reviews/{review_id}/finalize")
def finalize(review_id: str, _: dict = Signed) -> dict:
    return _answer(review_id, "finalize", [])


# AMP webhook (no session: signed by AMP) ------------------------------------------------------------------------


@app.post("/api/hitl/webhook")
async def hitl_webhook(request: Request) -> dict:
    s = server()
    body = await request.body()
    valid, how = webhook.verify(s.settings.hitl_webhook_secret.get_secret_value(), body, request.headers)
    log.warning("HITL webhook signature %s: %s", "accepted" if valid else "rejected", how)
    if not valid:
        raise HTTPException(status_code=401, detail="bad signature")
    payload = json.loads(body or b"{}")
    # Logged without values: AMP's real headers differed from its docs, so the real payload may too
    event = payload.get("event_type") or payload.get("event")  # AMP sends event_type; its docs say event
    log.warning("HITL webhook event %r, payload shape %s", event, webhook.shape(payload))
    # Any event is taken: what decides is a callback and a waiting review
    run_id, fde_id, day = webhook.run_reference(payload)
    match = s.store.waiting_for_run(run_id) if run_id else None
    if match is None and day and (fde_id or s.settings.fde_id) == s.settings.fde_id:
        latest = s.store.latest_review(day)
        match = latest if latest and latest.get("status") == "waiting" else None
    if match is None and run_id is None and day is None:
        waiting = s.store.waiting_reviews()  # no flow state in the payload: only an unambiguous match
        match = waiting[0] if len(waiting) == 1 else None
    callback_url = payload.get("callback_url")
    if match is None or not callback_url:
        log.warning(
            "HITL webhook for run %s, day %s did not match a waiting review (callback %s)",
            run_id,
            day,
            "present" if callback_url else "missing",
        )
        return {"ok": True, "matched": False}
    amp_request_id = str(payload.get("id") or (payload.get("request") or {}).get("id", ""))
    s.store.attach_callback(match["id"], amp_request_id, s.vault.seal(callback_url))
    log.warning("HITL webhook attached to review %s (round %s)", match["id"], match.get("round"))
    return {"ok": True, "matched": True}


# Learnings -----------------------------------------------------------------------------------------------------
# Short guidance the flow's LLM steps follow (fde_core.domain.learnings); only approved ones reach the crews.

LearningStep = Literal["attribution", "topics", "follow_ups"]
LearningStatus = Literal["proposed", "approved", "denied", "retired"]


class NewLearning(BaseModel):
    scope: str = "general"
    steps: list[LearningStep] = []
    text: str = Field(min_length=3, max_length=500)
    status: Literal["approved", "proposed"] = "approved"


class LearningChanges(BaseModel):
    scope: str | None = None
    steps: list[LearningStep] | None = None
    text: str | None = Field(default=None, min_length=3, max_length=500)
    status: LearningStatus | None = None


def _learning_accounts() -> dict[str, str]:
    """What a learning can be about: general, or an account of the latest day."""
    latest = next(iter(server().store.days(limit=1)), None)
    return {"general": "General"} | (accounts(server().store.day(str(latest["date"]))) if latest else {})


def _learning(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "scope", "steps", "text", "status", "source", "evidence")
    return {key: item.get(key) for key in keys} | {
        "created_at": _iso(item.get("created_at")),
        "updated_at": _iso(item.get("updated_at")),
    }


@app.get("/api/learnings")
def learnings(_: dict = Signed) -> dict[str, Any]:
    return {"learnings": [_learning(item) for item in server().store.learnings()], "accounts": _learning_accounts()}


@app.post("/api/learnings")
def add_learning(body: NewLearning, _: dict = Signed) -> dict[str, Any]:
    if body.scope not in _learning_accounts():
        raise HTTPException(status_code=400, detail=f"unknown scope {body.scope!r}")
    text = " ".join(body.text.split())
    return _learning(server().store.add_learning(body.scope, sorted(set(body.steps)), text, body.status))


@app.patch("/api/learnings/{learning_id}")
def edit_learning(learning_id: str, body: LearningChanges, _: dict = Signed) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    if "scope" in changes and changes["scope"] not in _learning_accounts():
        raise HTTPException(status_code=400, detail=f"unknown scope {changes['scope']!r}")
    if "text" in changes:
        changes["text"] = " ".join(changes["text"].split())
    if "steps" in changes:
        changes["steps"] = sorted(set(changes["steps"]))
    updated = server().store.update_learning(learning_id, changes)
    if updated is None:
        raise HTTPException(status_code=404, detail="no such learning")
    return _learning(updated)


# Reports -------------------------------------------------------------------------------------------------------
# Account reports: the app saves the request, the account_report flow (on AMP) writes the content, the app draws the
# preview and the PDF (report_render.py). Owned accounts only; only final days are read.

MAX_REPORT_DAYS = 93
STALE_RUN = timedelta(minutes=3)  # unfinished this long: ask AMP whether the run failed


class NewReport(BaseModel):
    accounts: list[str] = Field(min_length=1)
    start: date
    end: date
    language: Literal["en", "pt", "es"] = "en"
    comment: str = Field(default="", max_length=2000)


def _owned_accounts() -> dict[str, str]:
    latest = next(iter(server().store.days(limit=1)), None)
    names = accounts(server().store.day(str(latest["date"]))) if latest else {}
    return {key: name for key, name in names.items() if key not in (INTERNAL, UNASSIGNED)}


def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
    content = report.get("content") or {}
    return {
        "id": report["id"],
        "request": report["request"],
        "status": report.get("status"),
        "error": report.get("error"),
        "title": content.get("title"),
        "created_at": _iso(report.get("created_at")),
        "finished_at": _iso(report.get("finished_at")),
        "local_command": None
        if server().settings.amp_report_url
        else f'bin/flow account_report kickoff \'{{"report_id": "{report["id"]}"}}\'',
    }


def _amp(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    s = server().settings
    token = s.amp_report_token.get_secret_value() if s.amp_report_token else ""
    response = httpx.request(
        method,
        f"{s.amp_report_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def _check_run(report: dict[str, Any]) -> dict[str, Any]:
    """A run that should have finished: if AMP says it failed, the report says so (the flow could not)."""
    created = report.get("created_at")
    if (
        report.get("status") in ("queued", "running")
        and report.get("kickoff_id")
        and server().settings.amp_report_url
        and created
        and datetime.now(UTC) - created.replace(tzinfo=created.tzinfo or UTC) > STALE_RUN
    ):
        try:
            state = _amp("GET", f"/status/{report['kickoff_id']}")
        except httpx.HTTPError as error:
            log.warning("AMP status for report %s not read: %s", report["id"], type(error).__name__)
            return report
        if str(state.get("state", "")).lower() in ("failed", "revoked", "not found"):
            error = str(state.get("status") or state.get("state"))[:500]
            server().store.update_report(report["id"], {"status": "failed", "error": f"On AMP: {error}"})
            report = server().store.report(report["id"]) or report
    return report


def _done_report(report_id: str) -> dict[str, Any]:
    report = server().store.report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="no such report")
    if report.get("status") != "done" or not report.get("content"):
        raise HTTPException(status_code=409, detail="the report is not written yet")
    return report


@app.get("/api/reports")
def reports(_: dict = Signed) -> dict[str, Any]:
    return {
        "reports": [_report_summary(_check_run(item)) for item in server().store.reports()],
        "accounts": _owned_accounts(),
        "runs_on_amp": bool(server().settings.amp_report_url),
    }


@app.post("/api/reports")
def add_report(body: NewReport, _: dict = Signed) -> dict[str, Any]:
    owned = _owned_accounts()
    unknown = [key for key in body.accounts if key not in owned]
    if unknown:
        raise HTTPException(status_code=400, detail=f"not an owned account: {', '.join(unknown)}")
    if body.start > body.end:
        raise HTTPException(status_code=400, detail="the start date is after the end date")
    if (body.end - body.start).days >= MAX_REPORT_DAYS:
        raise HTTPException(status_code=400, detail=f"a report covers at most {MAX_REPORT_DAYS} days")
    request = {
        "accounts": list(dict.fromkeys(body.accounts)),
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "language": body.language,
        "comment": body.comment.strip(),
    }
    report = server().store.add_report(request)
    if server().settings.amp_report_url:
        try:
            kickoff = _amp("POST", "/kickoff", json={"inputs": {"report_id": report["id"]}})
        except httpx.HTTPError as error:
            server().store.update_report(report["id"], {"status": "failed", "error": "AMP did not start the run"})
            log.warning("AMP kickoff for report %s failed: %s", report["id"], type(error).__name__)
            raise HTTPException(status_code=502, detail="AMP did not start the report; try again") from error
        server().store.update_report(report["id"], {"kickoff_id": kickoff.get("kickoff_id")})
        report = server().store.report(report["id"]) or report
    return _report_summary(report)


@app.get("/api/reports/{report_id}")
def get_report(report_id: str, _: dict = Signed) -> dict[str, Any]:
    report = server().store.report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="no such report")
    return _report_summary(_check_run(report))


@app.get("/api/reports/{report_id}/html", response_class=HTMLResponse)
def report_preview(report_id: str, _: dict = Signed) -> str:
    return report_html(_done_report(report_id))


@app.get("/api/reports/{report_id}/pdf")
def report_download(report_id: str, _: dict = Signed) -> Response:
    report = _done_report(report_id)
    title = (report.get("content") or {}).get("title") or "Account report"
    name = "".join(char if char.isalnum() or char in " -_" else "" for char in title).strip()[:80] or "report"
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "report"  # for old clients
    return Response(
        report_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"{plain}.pdf\"; filename*=UTF-8''{quote(name)}.pdf"},
    )


@app.delete("/api/reports/{report_id}")
def delete_report(report_id: str, _: dict = Signed) -> dict[str, Any]:
    if not server().store.delete_report(report_id):
        raise HTTPException(status_code=404, detail="no such report")
    return {"deleted": report_id}


@app.get("/api/report-assets/{path:path}")
def report_asset(path: str, _: dict = Signed) -> FileResponse:
    """The preview's fonts and logo (the PDF reads them from disk)."""
    file = (ASSETS / path).resolve()
    if ASSETS not in file.parents or not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file)


# Frontend ------------------------------------------------------------------------------------------------------


def _dist() -> Path | None:
    configured = server().settings.web_dist
    candidate = Path(configured) if configured else Path(__file__).resolve().parents[2] / "web" / "dist"
    return candidate if (candidate / "index.html").exists() else None


_DIST = _dist()
if _DIST:
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "auth/")):
            raise HTTPException(status_code=404)
        file = (_DIST / path).resolve()
        if path and file.is_file() and _DIST in file.parents:
            return FileResponse(file)
        return FileResponse(_DIST / "index.html")
