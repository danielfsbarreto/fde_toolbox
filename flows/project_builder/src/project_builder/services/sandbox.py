"""A build's E2B sandbox: where the engineer writes the project, the checks run and the smoke test runs for real.

One sandbox per build (its id on Build.sandbox_id): resumed at each turn, paused at its end, and created again from
the `crewai-builder` template (with the latest code version's files) when E2B no longer has it. The build's env var
values reach commands as environment variables, never as a file in the project, and every output that leaves the
sandbox (to the agent, the activity log or MongoDB) has them replaced by their names.
"""

from __future__ import annotations

import logging
import posixpath
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass

from fde_core.domain.builds import CodeFile
from fde_core.vault import scrub

log = logging.getLogger("project_builder")

ALIAS = "crewai-builder"  # the template (flows/project_builder/sandbox/template.py)
WORK = "/home/user/work"
LOGS = "/home/user/.builder"
LIFETIME = 3600  # seconds a running sandbox lives without a call; paused between turns
OUTPUT_TAIL = 6_000  # characters of a command's output kept for the agent and the log
MAX_FILE = 2_000_000  # files larger than this are not stored in a code version (uv.lock is often ~1 MB)

# The latest CrewAI on PyPI against the installed one; upgrades only when they differ (template.py documents it)
FRESHEN = (
    "latest=$(curl -fsS https://pypi.org/pypi/crewai/json"
    ' | python3 -c \'import json,sys; print(json.load(sys.stdin)["info"]["version"])\')'
    ' && installed=$(uv tool list | sed -n "s/^crewai v//p")'
    ' && if [ "$latest" != "$installed" ]; then UV_COMPILE_BYTECODE=1 uv tool install -q "crewai==$latest"; fi'
    ' && echo "crewai $latest"'
)
# Never part of a code version: environments, caches, VCS, build output, and any .env
SKIP_DIRS = {
    ".venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
}
SKIP_FILES = re.compile(r"(^|/)\.env(\..*)?$")


@dataclass
class Result:
    exit_code: int
    output: str  # redacted, the end of it
    seconds: float


class BuildSandbox:
    """The sandbox of one build, for one turn. `values`: the build's env var values (decrypted, never logged)."""

    def __init__(self, package: str, values: dict[str, str], sandbox_id: str | None, api_key: str | None) -> None:
        self.package = package
        self.root = posixpath.join(WORK, package)
        self._values = values
        self._sandbox_id = sandbox_id
        self._api_key = api_key
        self._sbx = None
        self.fresh = False  # created this turn (vs resumed)
        self.crewai = ""
        self.implements: dict[str, list[str]] = {}  # the design elements each file written this turn implements

    # Lifecycle --------------------------------------------------------------------------------------------------

    def open(self, restore: list[CodeFile] | None = None) -> str:
        """Resume the build's sandbox, or create one (latest CrewAI, the given files restored). Returns its id."""
        from e2b import Sandbox  # pyright: ignore[reportMissingImports]  # the flow's own env (pyrightconfig.json)

        opts = {"api_key": self._api_key} if self._api_key else {}
        if self._sandbox_id:
            try:
                self._sbx = Sandbox.connect(self._sandbox_id, timeout=LIFETIME, **opts)
            except Exception as error:  # gone (expired or killed): a new one, from the stored code
                log.info("Sandbox %s not resumed (%s): creating one", self._sandbox_id, type(error).__name__)
                self._sbx = None
        if self._sbx is None:
            self._sbx = Sandbox.create(template=ALIAS, timeout=LIFETIME, **opts)
            self.fresh = True
            self._sbx.commands.run(f"mkdir -p {WORK} {LOGS}", timeout=30)
            freshen = self.run(FRESHEN, cwd=WORK, timeout=300)
            self.crewai = freshen.output.strip().splitlines()[-1] if freshen.output.strip() else ""
            for file in restore or []:
                self.write(file.path, file.content)
        else:
            version = self.run("uv tool list | sed -n 's/^crewai v//p'", cwd=WORK, timeout=60)
            self.crewai = f"crewai {version.output.strip()}" if version.output.strip() else ""
        return self._sbx.sandbox_id

    def pause(self) -> None:
        if self._sbx is not None:
            try:
                self._sbx.pause()
            except Exception as error:  # a sandbox that cannot pause expires on its own
                log.warning("Sandbox not paused: %s", type(error).__name__)

    def exists(self) -> bool:
        # From the work folder: the project's own folder doesn't exist before the scaffold, and a command whose
        # working directory is missing never starts (it would wait out its timeout)
        project = shlex.quote(posixpath.join(self.root, "pyproject.toml"))
        return self.run(f"test -f {project}", cwd=WORK, timeout=30).exit_code == 0

    # Commands ---------------------------------------------------------------------------------------------------

    def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 600,
        on_output: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> Result:
        """Run a shell command (in the project by default). Its output is tee'd to a file and its exit code saved,
        so a dropped stream (seen with E2B) is recovered from the files instead of failing the turn. `on_output`
        gets the output live, redacted, in batches."""
        assert self._sbx is not None, "open() the sandbox first"
        started = time.monotonic()
        name = f"{LOGS}/cmd-{int(started * 1000)}"
        script = f"{{ {command} ; }} 2>&1 | tee {name}.log; echo ${{PIPESTATUS[0]}} > {name}.exit"
        chunks: list[str] = []
        live = Batcher(on_output, self.redact, self._holdback()) if on_output else None

        def take(text: str) -> None:
            chunks.append(text)
            if live:
                live.feed(text)

        envs = {**self._values, **(env or {})}
        try:
            handle = self._sbx.commands.run(
                f"bash -c {shlex.quote(script)}", background=True, cwd=cwd or self.root, envs=envs, timeout=timeout
            )
            handle.wait(on_stdout=take, on_stderr=take)
        except Exception as error:  # a non-zero exit raises too; the files say what happened
            log.info("Command stream ended (%s); reading its files", type(error).__name__)
            self._wait_for(f"{name}.exit", started + timeout)
        if live:
            live.flush(final=True)
        exit_code = self._read_exit(f"{name}.exit")
        output = "".join(chunks)
        if not output or exit_code is None:
            output = self._read(f"{name}.log") or output
        return Result(
            exit_code=exit_code if exit_code is not None else 124,  # 124: timed out, as `timeout` reports it
            output=self.redact(output)[-OUTPUT_TAIL:],
            seconds=round(time.monotonic() - started, 1),
        )

    def _wait_for(self, path: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self._read(path) is not None:
                return
            time.sleep(2)

    def _read_exit(self, path: str) -> int | None:
        text = (self._read(path) or "").strip()
        return int(text) if text.lstrip("-").isdigit() else None

    def _read(self, path: str) -> str | None:
        assert self._sbx is not None
        try:
            return self._sbx.files.read(path)
        except Exception:
            return None

    # Files ------------------------------------------------------------------------------------------------------

    def path(self, relative: str) -> str:
        """A path in the project; nothing outside it."""
        full = posixpath.normpath(posixpath.join(self.root, relative.lstrip("/")))
        if full != self.root and not full.startswith(self.root + "/"):
            raise ValueError(f"{relative!r} is outside the project")
        return full

    def read(self, relative: str) -> str | None:
        return self._read(self.path(relative))

    def write(self, relative: str, content: str) -> None:
        assert self._sbx is not None
        self._sbx.files.write(self.path(relative), content)

    def write_outside(self, absolute: str, content: str) -> None:
        """A builder file outside the project (the checks' scripts): never part of a code version."""
        assert self._sbx is not None and absolute.startswith(LOGS + "/")
        self._sbx.files.write(absolute, content)

    def list(self, relative: str = ".") -> list[str]:
        """The project's files under a folder (relative paths), without environments and caches."""
        prune = " -o ".join(f"-name {shlex.quote(d)}" for d in sorted(SKIP_DIRS))
        result = self.run(
            f"find . \\( {prune} \\) -prune -o -type f -print | sed 's|^\\./||' | sort",
            cwd=self.path(relative),
            timeout=60,
        )
        return [line for line in result.output.splitlines() if line and not SKIP_FILES.search(line)]

    def snapshot(self) -> tuple[list[CodeFile], list[str], list[str]]:
        """Every text file of the project (redacted), the paths not stored (too large or not text), and the paths
        that held one of the build's values (stored redacted; a key belongs in the environment, not in code)."""
        files, skipped, leaked = [], [], []
        for relative in self.list():
            content = self.read(relative)
            if content is None or len(content) > MAX_FILE or "\x00" in content:
                skipped.append(relative)
                continue
            redacted = self.redact(content)
            if redacted != content:
                leaked.append(relative)
            files.append(CodeFile(path=relative, content=redacted))
        return files, skipped, leaked

    def redact(self, text: str) -> str:
        return scrub(text, self._values)

    def _holdback(self) -> int:
        return max((len(value) for value in self._values.values()), default=0)


class Batcher:
    """Live output in batches (every couple of seconds, or when large), redacted. Text is redacted as a whole before
    it is sent, and the end of it is held back: a value split across two chunks is only ever incomplete at the end,
    so no part of a value is sent."""

    def __init__(
        self, emit: Callable[[str], None], redact: Callable[[str], str], holdback: int, every: float = 2.0
    ) -> None:
        self._emit = emit
        self._redact = redact
        self._holdback = holdback
        self._every = every
        self._buffer = ""
        self._last = time.monotonic()

    def feed(self, text: str) -> None:
        self._buffer += text
        if time.monotonic() - self._last >= self._every or len(self._buffer) > 4_000:
            self.flush()

    def flush(self, final: bool = False) -> None:
        redacted = self._redact(self._buffer)
        cut = len(redacted) if final else max(0, len(redacted) - self._holdback)
        if not final:
            newline = redacted.rfind("\n", 0, cut)
            cut = newline + 1 if newline >= 0 else 0  # whole lines only, while more comes
        out, self._buffer = redacted[:cut], redacted[cut:]
        self._last = time.monotonic()
        if out:
            self._emit(out)
