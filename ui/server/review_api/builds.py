"""The Builds API: CrewAI projects built conversationally by the project_builder flow, spec first.

The app creates builds and stores the user's messages; each message is one chat turn of the flow, sent to AMP's
Conversational Flow Chat API when AMP_BUILDER_URL is set, else left queued for a local worker. The flow writes the
replies, artifacts and activity to MongoDB, and the UI polls them here (one timeline per build: messages and activity
share its sequence numbers). One turn at a time per build; builds run side by side.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from review_api.auth import current_user
from review_api.store import BUILD_STAGES, new_id
from review_api.wiring import server

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/builds")
Signed = Depends(current_user)

Stage = Literal["spec", "design", "code", "test"]
CHECK_AFTER = timedelta(minutes=3)  # a turn running this long: ask AMP now and then whether it failed
CHECK_EVERY = 30.0  # seconds between those checks, per build
_checked: dict[str, float] = {}


class NewBuild(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    account: str | None = Field(default=None, max_length=80)


class CodeRef(BaseModel):
    path: str
    start: int
    end: int


class Answer(BaseModel):
    option: str | None = None
    text: str = Field(default="", max_length=5_000)


class NewMessage(BaseModel):
    text: str = Field(default="", max_length=20_000)
    action: Literal["approve", "request_changes", "smoke"] | None = None
    stage: Stage | None = None
    version: int | None = None
    ref: CodeRef | None = None
    request_id: str | None = None  # with answers: the form they answer
    answers: dict[str, Answer] | None = None


def _missing(request: dict[str, Any], answers: dict[str, Answer]) -> list[str]:
    """Questions without an answer: a valid option, the user's own words, or both; a smoke test skipped with a reason
    needs nothing else (fde_core's missing_answers)."""
    if request.get("kind") == "smoke" and "skip" in answers:
        return [] if answers["skip"].text.strip() else ["skip"]
    missing = []
    for question in request.get("questions", []):
        answer = answers.get(question["id"])
        values = {option["value"] for option in question.get("options", [])}
        if answer is None or not (answer.option in values or answer.text.strip()):
            missing.append(question["id"])
    return missing


def _check_answers(build: dict[str, Any], body: NewMessage) -> None:
    """While a form is open, only its complete answers go through: the agent continues once all are answered."""
    pending = build.get("pending_input")
    if body.answers is None:
        if pending and pending.get("kind") != "smoke":  # a smoke test's form doesn't hold the build (fde_core)
            raise HTTPException(status_code=409, detail="Answer the open questions first.")
        return
    if not pending or body.request_id != pending["request_id"]:
        raise HTTPException(status_code=400, detail="These answers are for a form that is no longer open.")
    asked = server().store.build_message(build["id"], pending["message_id"])
    missing = _missing((asked or {}).get("input_request") or {}, body.answers)
    if missing:
        raise HTTPException(status_code=400, detail=f"Answer every question first (missing: {', '.join(missing)}).")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _runs_on_amp() -> bool:
    s = server().settings
    return bool(s.amp_builder_url and s.amp_builder_token)


def _amp(method: str, path: str, timeout: float = 30, **kwargs: Any) -> dict[str, Any]:
    s = server().settings
    token = s.amp_builder_token.get_secret_value() if s.amp_builder_token else ""
    response = httpx.request(
        method,
        f"{(s.amp_builder_url or '').rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def _status(build: dict[str, Any]) -> str:
    """working: a turn is in progress; waiting: a form, a gate or a stale stage waits for the FDE; idle (fde_core's)."""
    if build.get("turn"):
        return "working"
    if build.get("pending_input") or any(
        stage.get("status") in ("draft", "stale") for stage in build.get("stages", {}).values()
    ):
        return "waiting"
    return "idle"


def _env_needed(build_id: str) -> list[dict[str, Any]]:
    """The env vars the latest design needs (names and what for; values live in build_env)."""
    design = server().store.artifact(build_id, "design", None)
    if design is None:
        return []
    return [
        {
            "name": var["name"],
            "purpose": var.get("purpose", ""),
            "secret": var.get("secret", True),
            "required": var.get("required", True),  # designs from before `required`: all required
        }
        for var in design["content"].get("env_vars", [])
    ]


def _env_missing(build_id: str, needed: list[dict[str, Any]] | None = None) -> list[str]:
    """The required ones without a value: the design cannot be approved until they are set (fde_core's missing_env)."""
    needed = _env_needed(build_id) if needed is None else needed
    have = {doc["name"] for doc in server().store.build_env(build_id)}
    return [var["name"] for var in needed if var["required"] and var["name"] not in have]


def _smoke(build_id: str) -> dict[str, Any] | None:
    code = server().store.artifact(build_id, "code", None)
    if code is None:
        return None
    smoke = code["content"].get("smoke") or {}
    return {"version": code["version"], "status": smoke.get("status", "waiting"), "reason": smoke.get("reason", "")}


def _summary(build: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    status = _status(build)
    needed = _env_needed(build["id"]) if detail else None
    last = server().store.last_activity(build["id"]) if status == "working" else None
    return {
        "id": build["id"],
        "title": build["title"],
        "account": build.get("account"),
        "status": status,
        "now": last["text"] if last else None,  # what the agent is doing, while working
        "current": build.get("current", "spec"),
        "stages": {stage: build["stages"][stage] for stage in BUILD_STAGES},
        "pending_input": build.get("pending_input"),  # a form waiting for the user's answers
        # The values the design needs, and those still missing before it can be approved (one build at a time, not
        # in the list)
        "env_needed": needed,
        "env_missing": _env_missing(build["id"], needed) if needed is not None else None,
        # The latest code version's smoke test (its status gates approving the code)
        "smoke": _smoke(build["id"]) if detail else None,
        "seq": build.get("seq", 0),
        "created_at": _iso(build.get("created_at")),
        "updated_at": _iso(build.get("updated_at")),
    }


def _message(message: dict[str, Any]) -> dict[str, Any]:
    return {**message, "created_at": _iso(message.get("created_at"))}


def _entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "at": _iso(entry.get("at"))}


def _build(build_id: str) -> dict[str, Any]:
    build = server().store.build(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="no such build")
    return _check_turn(build)


def _check_turn(build: dict[str, Any]) -> dict[str, Any]:
    """A turn running for a while on AMP: if AMP says its run failed, end it (the flow could not)."""
    turn = build.get("turn")
    if not (turn and turn.get("kickoff_id") and _runs_on_amp()):
        return build
    started = turn["started_at"].replace(tzinfo=turn["started_at"].tzinfo or UTC)
    now = time.monotonic()
    if datetime.now(UTC) - started < CHECK_AFTER or now - _checked.get(build["id"], 0) < CHECK_EVERY:
        return build
    _checked[build["id"]] = now
    try:
        state = _amp("GET", f"/status/{turn['kickoff_id']}")
    except httpx.HTTPError as error:
        log.warning("AMP status for build %s not read: %s", build["id"], type(error).__name__)
        return build
    if str(state.get("state", "")).lower() in ("failed", "revoked", "not found"):
        store = server().store
        reason = str(state.get("status") or state.get("state"))[:300]
        store.add_builder_note(build["id"], turn["id"], f"The turn failed on AMP: {reason}")
        store.set_delivery(build["id"], turn["id"], "failed")
        store.end_turn(build["id"], turn["id"])
        build = store.build(build["id"]) or build
    return build


@router.get("")
def list_builds(_: dict = Signed) -> dict[str, Any]:
    builds = [_summary(_check_turn(build)) for build in server().store.builds()]
    return {
        "builds": builds,
        "waiting": sum(1 for build in builds if build["status"] == "waiting"),
        "runs_on_amp": _runs_on_amp(),
        "local_command": None if _runs_on_amp() else "bin/flow project_builder worker",
    }


@router.post("")
def add_build(body: NewBuild, _: dict = Signed) -> dict[str, Any]:
    build_id = new_id()
    session_id = build_id  # a local worker's chat session is the build's own
    if _runs_on_amp():
        try:
            session_id = str(_amp("POST", "/chat/start", json={})["session_id"])
        except (httpx.HTTPError, KeyError) as error:
            log.warning("AMP chat session not started: %s", type(error).__name__)
            raise HTTPException(status_code=502, detail="AMP did not start the chat session; try again") from error
    account = (body.account or "").strip() or None
    return _summary(server().store.add_build(build_id, body.title.strip(), account, session_id))


@router.get("/{build_id}")
def timeline(build_id: str, after: int = 0, _: dict = Signed) -> dict[str, Any]:
    """The build and its timeline since `after` (a sequence number): what the UI polls."""
    build = _build(build_id)
    store = server().store
    return {
        "build": _summary(build, detail=True),
        "messages": [_message(message) for message in store.build_messages(build_id, after)],
        "activity": [_entry(entry) for entry in store.build_activity(build_id, after)],
    }


@router.get("/{build_id}/artifacts/{stage}")
def artifact(build_id: str, stage: Stage, version: int | None = None, _: dict = Signed) -> dict[str, Any]:
    _build(build_id)
    store = server().store
    found = store.artifact(build_id, stage, version)
    versions = [
        {**item, "created_at": _iso(item.get("created_at"))} for item in store.artifact_versions(build_id, stage)
    ]
    if found is None:
        return {"artifact": None, "versions": versions}
    return {"artifact": {**found, "created_at": _iso(found.get("created_at"))}, "versions": versions}


@router.get("/{build_id}/code.zip")
def code_zip(build_id: str, version: int | None = None, _: dict = Signed) -> Response:
    """A code version as a ZIP: its files as stored (values redacted), and a .env.example naming the environment
    variables the design needs, never their values."""
    build = _build(build_id)
    code = server().store.artifact(build_id, "code", version)
    if code is None:
        raise HTTPException(status_code=404, detail="no code yet")
    content = code["content"]
    package = content.get("package") or "project"
    design = server().store.artifact(build_id, "design", (build["stages"]["design"] or {}).get("approved_version"))
    names = [var["name"] for var in ((design or {}).get("content") or {}).get("env_vars", [])]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in content.get("files", []):
            archive.writestr(f"{package}/{file['path']}", file["content"])
        if names:
            archive.writestr(f"{package}/.env.example", "".join(f"{name}=\n" for name in names))
    filename = f"{package}-v{code['version']}.zip"
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{build_id}/messages")
def send(build_id: str, body: NewMessage, _: dict = Signed) -> dict[str, Any]:
    build = _build(build_id)
    if not body.text.strip() and not body.action and body.answers is None:
        raise HTTPException(status_code=400, detail="an empty message")
    _check_answers(build, body)
    if body.action == "approve" and body.stage == "design":
        missing = _env_missing(build["id"])
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"Set the environment variables {', '.join(missing)} first (on the design's card).",
            )
    store = server().store
    on_amp = _runs_on_amp()
    if on_amp and build.get("turn"):
        raise HTTPException(status_code=409, detail="The agent is still answering the last message.")
    envelope = body.model_dump(exclude_none=True)
    text = body.text.strip() or body.action or "Answers"
    # Queued messages are a local worker's to run; one sent to AMP must never be picked up by a worker too
    message = store.add_user_message(
        build_id,
        text,
        envelope,
        delivery="sending" if on_amp else "queued",
        answers_for=body.request_id if body.answers is not None else None,
    )
    if not on_amp:
        return _message({k: v for k, v in message.items() if k != "envelope"})  # a local worker picks it up

    if not store.start_turn(build_id, message["id"]):
        store.set_delivery(build_id, message["id"], "failed")
        raise HTTPException(status_code=409, detail="The agent is still answering the last message.")
    try:
        turn = _send_turn(build, message["envelope"])
    except httpx.HTTPError as error:
        store.end_turn(build_id, message["id"])
        store.set_delivery(build_id, message["id"], "failed")
        busy = isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        log.warning("AMP chat turn for build %s not sent: %s %s", build_id, type(error).__name__, status or "")
        detail = "AMP is still answering the last message." if busy else "AMP did not take the message; try again"
        raise HTTPException(status_code=409 if busy else 502, detail=detail) from error
    store.set_turn_kickoff(build_id, message["id"], turn.get("kickoff_id"))
    store.set_delivery(build_id, message["id"], "sent")
    message["delivery"] = "sent"
    return _message({k: v for k, v in message.items() if k != "envelope"})


ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")


class EnvValue(BaseModel):
    value: str = Field(min_length=1, max_length=10_000)


class ReuseEnv(BaseModel):
    from_build: str = Field(min_length=1, max_length=40)


def _env_name(name: str) -> str:
    if not ENV_NAME.match(name):
        raise HTTPException(status_code=400, detail="an env var name is uppercase letters, digits and underscores")
    return name


def _env(build_id: str) -> dict[str, Any]:
    store = server().store
    return {
        "set": [{"name": doc["name"], "set_at": _iso(doc.get("set_at"))} for doc in store.build_env(build_id)],
        "elsewhere": store.env_elsewhere(build_id),
        "can_store": server().env_vault is not None,
    }


@router.get("/{build_id}/env")
def build_env(build_id: str, _: dict = Signed) -> dict[str, Any]:
    """Which of the project's env vars have a value (never the values), and other builds that have one to reuse."""
    _build(build_id)
    return _env(build_id)


@router.put("/{build_id}/env/{name}")
def set_build_env(build_id: str, name: str, body: EnvValue, _: dict = Signed) -> dict[str, Any]:
    _build(build_id)
    vault = server().env_vault
    if vault is None:
        raise HTTPException(
            status_code=503, detail="Environment variables cannot be stored yet: BUILD_ENV_KEY is not set"
        )
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="the value is empty")
    server().store.set_build_env(build_id, _env_name(name), vault.seal(value))
    return _env(build_id)


@router.post("/{build_id}/env/{name}/reuse")
def reuse_build_env(build_id: str, name: str, body: ReuseEnv, _: dict = Signed) -> dict[str, Any]:
    _build(build_id)
    _build(body.from_build)  # one of yours
    if not server().store.reuse_build_env(build_id, _env_name(name), body.from_build):
        raise HTTPException(status_code=404, detail=f"that build has no value for {name}")
    return _env(build_id)


@router.delete("/{build_id}/env/{name}")
def delete_build_env(build_id: str, name: str, _: dict = Signed) -> dict[str, Any]:
    _build(build_id)
    server().store.delete_build_env(build_id, _env_name(name))
    return _env(build_id)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def _send_turn(build: dict[str, Any], envelope: str) -> dict[str, Any]:
    """One chat turn on AMP. A session AMP no longer has (a redeploy drops them) is replaced by a new one: the build's
    state is all in MongoDB and the flow finds the build by the envelope's build_id, so nothing is lost."""
    body = {"message": envelope, "stream": False}
    try:  # a deployment idle for hours can take a while to answer its first call
        return _amp("POST", f"/chat/{build['session_id']}/message", timeout=90, json=body)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
    session_id = str(_amp("POST", "/chat/start", json={})["session_id"])
    server().store.set_session(build["id"], session_id)
    log.info("Build %s: AMP lost its chat session; started a new one", build["id"])
    return _amp("POST", f"/chat/{session_id}/message", timeout=90, json=body)


@router.patch("/{build_id}")
def rename_build(build_id: str, body: Rename, _: dict = Signed) -> dict[str, Any]:
    _build(build_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="a build needs a title")
    server().store.rename_build(build_id, title)
    return _summary(_build(build_id))


@router.delete("/{build_id}")
def delete_build(build_id: str, _: dict = Signed) -> dict[str, Any]:
    build = _build(build_id)
    if build.get("turn"):
        raise HTTPException(status_code=409, detail="The agent is answering; delete the build when it is done.")
    server().store.delete_build(build)
    return {"deleted": build_id}
