"""The engineer's tools, bound to one build's sandbox for one turn. Each logs what it did as a step of the turn's
timeline (a file written, with its lines added and removed and the design elements it implements; a command, with
its exit code, time and output), redacted. The engineer never runs the project itself: the smoke test does that,
with the user's inputs."""

from __future__ import annotations

import difflib
import re

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from project_builder.activity import Activity
from project_builder.services.sandbox import BuildSandbox

LIVE_FILE = 100_000  # characters of a file kept in its timeline step (the Code tab shows it while the turn runs)
READ_LIMIT = 30_000
NOT_HERE = re.compile(r"\bcrewai\s+(run|deploy|login|train|test|chat)\b|\bkickoff\b")


class _SandboxTool(BaseTool):
    _sandbox: BuildSandbox = PrivateAttr()
    _activity: Activity = PrivateAttr()

    def bind(self, sandbox: BuildSandbox, activity: Activity) -> _SandboxTool:
        self._sandbox, self._activity = sandbox, activity
        return self


class WriteFileInput(BaseModel):
    path: str = Field(description="Relative to the project root, e.g. src/news_digest/main.py")
    content: str = Field(description="The whole file")
    implements: list[str] = Field(default=[], description="The design elements (D-n) this file implements")


class WriteFile(_SandboxTool):
    name: str = "write_file"
    description: str = (
        "Write a whole file of the project (created or replaced). Say which design elements (D-n) it implements. "
        "Never put a key or token in a file: the project reads them from environment variables."
    )
    args_schema: type[BaseModel] = WriteFileInput

    def _run(self, path: str, content: str, implements: list[str] | None = None) -> str:
        before = self._sandbox.read(path)
        self._sandbox.write(path, content)
        self._sandbox.implements[path] = list(implements or [])
        old, new = (before or "").splitlines(), content.splitlines()
        diff = list(difflib.unified_diff(old, new, lineterm="", n=0))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        shown = self._sandbox.redact(content)
        self._activity.step(
            "file",
            f"{'~' if before is not None else '+'} {path}",
            path=path,
            op="changed" if before is not None else "added",
            added=added,
            removed=removed,
            implements=list(implements or []),
            content=shown[:LIVE_FILE],
        )
        return f"Wrote {path} (+{added} −{removed})."


class ReadFileInput(BaseModel):
    path: str = Field(description="Relative to the project root")


class ReadFile(_SandboxTool):
    name: str = "read_file"
    description: str = "Read a file of the project."
    args_schema: type[BaseModel] = ReadFileInput

    def _run(self, path: str) -> str:
        content = self._sandbox.read(path)
        if content is None:
            return f"{path} does not exist."
        content = self._sandbox.redact(content)
        return content if len(content) <= READ_LIMIT else content[:READ_LIMIT] + "\n… (cut: the file is longer)"


class ListFilesInput(BaseModel):
    path: str = Field(default=".", description="A folder relative to the project root")


class ListFiles(_SandboxTool):
    name: str = "list_files"
    description: str = "List the project's files under a folder (without .venv and caches)."
    args_schema: type[BaseModel] = ListFilesInput

    def _run(self, path: str = ".") -> str:
        return "\n".join(self._sandbox.list(path)) or "(no files)"


class RunCommandInput(BaseModel):
    command: str = Field(description="A shell command, run in the project root, e.g. `uv add httpx` or `uv run pytest`")
    timeout_seconds: int = Field(default=300, ge=10, le=900)


class RunCommand(_SandboxTool):
    name: str = "run_command"
    description: str = (
        "Run a shell command in the project's sandbox: install packages (uv add), run the tests (uv run pytest), "
        "inspect things. Not for running the project itself: the smoke test does that with the user's inputs."
    )
    args_schema: type[BaseModel] = RunCommandInput

    def _run(self, command: str, timeout_seconds: int = 300) -> str:
        if NOT_HERE.search(command):
            return "Not run: the project runs for real only in the smoke test, with the user's inputs."
        result = self._sandbox.run(command, timeout=timeout_seconds)
        self._activity.step(
            "command",
            f"$ {command[:200]}",
            command=command,
            exit=result.exit_code,
            seconds=result.seconds,
            output=result.output[-4_000:],
        )
        return f"exit {result.exit_code} ({result.seconds}s)\n{result.output}"


class NoteInput(BaseModel):
    text: str = Field(description="One or two sentences: a decision and why, for the user following along")


class Note(_SandboxTool):
    name: str = "note"
    description: str = "Tell the user, in one or two sentences, a decision you made and why (shown in the timeline)."
    args_schema: type[BaseModel] = NoteInput

    def _run(self, text: str) -> str:
        self._activity.step("note", text[:400])
        return "Noted."


def sandbox_tools(sandbox: BuildSandbox, activity: Activity, read_only: bool = False) -> list[BaseTool]:
    tools: list[_SandboxTool] = [ReadFile(), ListFiles()]
    if not read_only:
        tools += [WriteFile(), RunCommand(), Note()]
    return [tool.bind(sandbox, activity) for tool in tools]
