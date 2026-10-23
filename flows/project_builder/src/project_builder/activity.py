"""A turn's activity: what the UI shows while an agent works, and the router log.

Agents' tool calls (loading a skill, sandbox commands) arrive as CrewAI events on a process-wide bus; they are routed
to the activity of the turn whose agent made them, by agent id, so turns running side by side never mix.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from crewai.events import ToolUsageStartedEvent, crewai_event_bus

from fde_core.domain.builds import ActivityKind, Build, Phase
from fde_core.repositories.builds import MongoBuildRepository


class Activity:
    def __init__(self, builds: MongoBuildRepository, build: Build, turn_id: str | None) -> None:
        self._builds = builds
        self._build = build
        self._turn_id = turn_id
        self.phase: Phase | None = None  # a code turn's current phase: its steps (and tool calls) belong to it

    def log(self, kind: ActivityKind, text: str, **detail: Any) -> None:
        if self.phase and "phase" not in detail:
            detail["phase"] = self.phase
        self._builds.add_activity(self._build, kind, text, turn_id=self._turn_id, detail=detail)

    # A code turn's timeline -------------------------------------------------------------------------------------

    def begin(self, phase: Phase, text: str, **detail: Any) -> None:
        self.phase = phase
        self.log("phase", text, phase=phase, state="running", **detail)

    def end(self, text: str, failed: bool = False, **detail: Any) -> None:
        if self.phase:
            self.log("phase", text, phase=self.phase, state="failed" if failed else "done", **detail)

    def step(self, step: str, text: str, **detail: Any) -> None:
        """One thing done in the current phase: a command, a file written, a check, live output, a note."""
        self.log("step", text, step=step, **detail)

    @contextmanager
    def watching(self, agent: Any) -> Iterator[None]:
        """Log the agent's tool calls while it works."""
        key = str(agent.id)
        with _lock:
            _sinks[key] = self
        try:
            yield
        finally:
            crewai_event_bus.flush()
            with _lock:
                _sinks.pop(key, None)


_sinks: dict[str, Activity] = {}
_lock = threading.Lock()


SELF_LOGGED = {"write_file", "run_command"}  # sandbox tools that log their own steps, with details


def _describe(event: ToolUsageStartedEvent) -> str:
    args = event.tool_args if isinstance(event.tool_args, dict) else {}
    if event.tool_name == "load_skill" and args.get("skill_name"):
        return f"Loaded the {args['skill_name']} skill"
    return f"Used {event.tool_name}"


@crewai_event_bus.on(ToolUsageStartedEvent)
def _on_tool(source: Any, event: ToolUsageStartedEvent) -> None:
    with _lock:
        sink = _sinks.get(str(event.agent_id))
    if sink and event.tool_name not in SELF_LOGGED:
        sink.log("tool", _describe(event), tool=event.tool_name)
