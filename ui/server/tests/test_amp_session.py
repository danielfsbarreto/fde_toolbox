"""A chat session AMP no longer has (after a redeploy) is replaced, and the message still goes through."""

from types import SimpleNamespace

import httpx
import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from review_api import builds
from review_api.auth import current_user
from review_api.store import Store

STAGES = {
    s: {"status": "not_started", "version": 0, "approved_version": None} for s in ("spec", "design", "code", "test")
}


@pytest.fixture
def setup(monkeypatch):
    db = mongomock.MongoClient().db
    db.builds.insert_one(
        {"id": "b1", "fde_id": "me", "title": "t", "turn": None, "session_id": "old", "seq": 0, "stages": STAGES}
    )
    fake = SimpleNamespace(store=Store(db, "me"), env_vault=None, settings=SimpleNamespace())
    monkeypatch.setattr(builds, "server", lambda: fake)
    monkeypatch.setattr(builds, "_runs_on_amp", lambda: True)
    calls: list[str] = []

    def amp(method, path, **kwargs):
        calls.append(path)
        if path == "/chat/old/message":
            request = httpx.Request(method, "https://amp" + path)
            raise httpx.HTTPStatusError("gone", request=request, response=httpx.Response(404, request=request))
        if path == "/chat/start":
            return {"session_id": "new"}
        return {"kickoff_id": "k1"}

    monkeypatch.setattr(builds, "_amp", amp)
    app = FastAPI()
    app.include_router(builds.router)
    app.dependency_overrides[current_user] = lambda: {"login": "me"}
    return TestClient(app), db, calls


def test_a_lost_session_is_replaced_and_the_message_sent(setup):
    client, db, calls = setup
    response = client.post("/api/builds/b1/messages", json={"text": "Use Exa instead"})
    assert response.status_code == 200 and response.json()["delivery"] == "sent"
    assert calls == ["/chat/old/message", "/chat/start", "/chat/new/message"]
    assert db.builds.find_one({"id": "b1"})["session_id"] == "new"
