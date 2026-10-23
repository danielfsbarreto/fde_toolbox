"""A build's env var values: stored encrypted, never returned, scoped to the FDE.

Run: cd ui/server && uv run --with pytest --with mongomock pytest -q tests
"""

from types import SimpleNamespace

import mongomock
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from review_api import builds
from review_api.auth import current_user
from review_api.env_vault import EnvVault
from review_api.store import Store

KEY = Fernet.generate_key().decode()


@pytest.fixture
def setup(monkeypatch):
    db = mongomock.MongoClient().db
    for fde, build_id, title in (("me", "b1", "news"), ("me", "b2", "leads"), ("someone", "x9", "theirs")):
        db.builds.insert_one({"id": build_id, "fde_id": fde, "title": title, "turn": None})
    db.build_artifacts.insert_one(
        {
            "fde_id": "me",
            "build_id": "b1",
            "stage": "design",
            "version": 1,
            "content": {"kind": "design", "env_vars": [{"name": "EXA_API_KEY"}, {"name": "X", "required": False}]},
        }
    )
    fake = SimpleNamespace(store=Store(db, "me"), env_vault=EnvVault(KEY), settings=SimpleNamespace())
    monkeypatch.setattr(builds, "server", lambda: fake)
    monkeypatch.setattr(builds, "_runs_on_amp", lambda: False)
    app = FastAPI()
    app.include_router(builds.router)
    app.dependency_overrides[current_user] = lambda: {"login": "me"}
    return TestClient(app), db, fake


def test_a_value_is_stored_encrypted_and_never_returned(setup):
    client, db, _ = setup
    response = client.put("/api/builds/b1/env/EXA_API_KEY", json={"value": " exa-secret-123 "})
    assert response.status_code == 200
    assert "exa-secret-123" not in response.text
    assert [item["name"] for item in response.json()["set"]] == ["EXA_API_KEY"]
    stored = db.build_env.find_one({"build_id": "b1"})
    assert stored["fde_id"] == "me" and "exa-secret-123" not in str(stored)
    assert Fernet(KEY.encode()).decrypt(stored["ciphertext"].encode()).decode() == "exa-secret-123"
    assert "exa-secret-123" not in client.get("/api/builds/b1/env").text


def test_reuse_copies_from_your_own_builds_only(setup):
    client, db, fake = setup
    client.put("/api/builds/b2/env/SERPER_API_KEY", json={"value": "serper-abc-123"})
    db.build_env.insert_one({"fde_id": "someone", "build_id": "x9", "name": "OPENAI_API_KEY", "ciphertext": "c"})
    listed = client.get("/api/builds/b1/env").json()
    assert listed["elsewhere"] == {"SERPER_API_KEY": [{"build_id": "b2", "title": "leads"}]}  # not someone's
    assert client.post("/api/builds/b1/env/SERPER_API_KEY/reuse", json={"from_build": "b2"}).status_code == 200
    assert fake.store.build_env("b1")[0]["name"] == "SERPER_API_KEY"
    assert client.post("/api/builds/b1/env/OPENAI_API_KEY/reuse", json={"from_build": "x9"}).status_code == 404


def test_names_are_checked_and_values_can_be_removed(setup):
    client, _, fake = setup
    assert client.put("/api/builds/b1/env/bad-name", json={"value": "v"}).status_code == 400
    assert client.get("/api/builds/x9/env").status_code == 404  # another FDE's build
    client.put("/api/builds/b1/env/SLACK_CHANNEL", json={"value": "#leads"})
    assert client.delete("/api/builds/b1/env/SLACK_CHANNEL").json()["set"] == []


def test_without_a_key_values_are_not_stored(setup):
    client, _, fake = setup
    fake.env_vault = None
    response = client.put("/api/builds/b1/env/EXA_API_KEY", json={"value": "v"})
    assert response.status_code == 503 and "BUILD_ENV_KEY" in response.json()["detail"]
    assert client.get("/api/builds/b1/env").json()["can_store"] is False


def test_a_design_is_not_approved_while_a_required_value_is_missing(setup):
    client, _, _ = setup
    approve = {"action": "approve", "stage": "design", "version": 1}
    blocked = client.post("/api/builds/b1/messages", json=approve)
    assert blocked.status_code == 409 and "Set the environment variables EXA_API_KEY first" in blocked.json()["detail"]
    client.put("/api/builds/b1/env/EXA_API_KEY", json={"value": "exa-secret-123"})  # X is optional
    assert client.post("/api/builds/b1/messages", json=approve).status_code == 200


def test_a_code_version_downloads_as_a_zip_without_values(setup):
    client, db, _ = setup
    db.builds.update_one({"id": "b1"}, {"$set": {"stages": {"design": {"approved_version": 1}}}})
    db.build_artifacts.insert_one(
        {
            "fde_id": "me",
            "build_id": "b1",
            "stage": "code",
            "version": 2,
            "content": {
                "kind": "code",
                "package": "news",
                "files": [{"path": "src/news/main.py", "content": "KEY = '[redacted:EXA_API_KEY]'"}],
            },
        }
    )
    response = client.get("/api/builds/b1/code.zip")
    assert response.status_code == 200 and 'filename="news-v2.zip"' in response.headers["content-disposition"]
    import io
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ["news/.env.example", "news/src/news/main.py"]
    assert archive.read("news/.env.example").decode() == "EXA_API_KEY=\nX=\n"
