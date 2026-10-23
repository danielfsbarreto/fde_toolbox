"""An in-memory stand-in for a build's E2B sandbox: files in a dict, commands answered by script."""

import json
from typing import ClassVar

from fde_core.domain.builds import CodeFile
from fde_core.vault import scrub
from project_builder.services.checks import MARK
from project_builder.services.sandbox import Result


class FakeSandbox:
    # What the next commands answer, set per test
    structure: ClassVar[dict | None] = None
    smoke_runs: ClassVar[list[tuple[int, str]]] = []  # (exit code, output) per smoke run, in order
    last: ClassVar["FakeSandbox | None"] = None

    def __init__(self, package, values, sandbox_id, api_key):
        self.package = package
        self.values = values
        self._id = sandbox_id or "sbx-fake"
        self.fresh = sandbox_id is None
        self.crewai = "crewai 9.9.9"
        self.implements: dict[str, list[str]] = {}
        self.files: dict[str, str] = {}
        self.commands: list[str] = []
        self.envs: list[dict] = []
        self.paused = False
        FakeSandbox.last = self

    def open(self, restore=None):
        for file in restore or []:
            self.files[file.path] = file.content
        return self._id

    def pause(self):
        self.paused = True

    def exists(self):
        return "pyproject.toml" in self.files

    def run(self, command, cwd=None, timeout=600, on_output=None, env=None):
        self.commands.append(command)
        self.envs.append({**self.values, **(env or {})})
        if command.startswith("crewai create"):
            self.files["pyproject.toml"] = "[project]\nname = 'x'\n"
            self.files[f"src/{self.package}/main.py"] = "# scaffold\n"
            return Result(0, "created", 0.1)
        if "structure.py" in command:
            return Result(0, MARK + json.dumps(FakeSandbox.structure), 0.2)
        if "smoke.py" in command:
            code, output = FakeSandbox.smoke_runs.pop(0) if FakeSandbox.smoke_runs else (0, MARK + "done")
            if on_output:
                on_output(self.redact(output))
            return Result(code, self.redact(output), 1.5)
        return Result(0, "", 0.1)

    def write_outside(self, path, content):
        pass

    def read(self, path):
        return self.files.get(path)

    def write(self, path, content):
        self.files[path] = content

    def list(self, relative="."):
        return sorted(self.files)

    def snapshot(self):
        files, leaked = [], []
        for path, content in sorted(self.files.items()):
            redacted = self.redact(content)
            if redacted != content:
                leaked.append(path)
            files.append(CodeFile(path=path, content=redacted))
        return files, [], leaked

    def redact(self, text):
        return scrub(text, self.values)
