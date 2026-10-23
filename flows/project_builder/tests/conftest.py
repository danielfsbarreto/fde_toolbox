import os
import socket
from pathlib import Path

# Absolute path, before crewai is imported: its local files land here, not in the user data dir
os.environ["CREWAI_STORAGE_DIR"] = str(Path(__file__).parent / ".crewai-test-storage")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise RuntimeError("test tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def no_real_sandbox(monkeypatch):
    """Every test runs the code stage against an in-memory sandbox and a stub engineer that writes nothing."""
    from fake_sandbox import FakeSandbox
    from stub_llm import StubLLM

    from project_builder import agents
    from project_builder.drafts import CodeTurn
    from project_builder.services import builder

    FakeSandbox.structure, FakeSandbox.smoke_runs, FakeSandbox.last = None, [], None
    monkeypatch.setattr(builder, "BuildSandbox", FakeSandbox)
    original = agents.engineer
    default = StubLLM(
        default_response="Final Answer: " + CodeTurn(reply="Wrote it.", summary="First").model_dump_json()
    )

    def engineer(model, tools):
        agent = original(model, tools)
        agent.llm = default
        return agent

    monkeypatch.setattr(agents, "engineer", engineer)
