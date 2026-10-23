from __future__ import annotations

from typing import Any, Protocol


class ActionError(RuntimeError):
    """An integration action failed. The message never carries payload content."""

    def __init__(self, app: str, action: str, code: str, detail: str = "") -> None:
        self.app, self.action, self.code = app, action, code
        super().__init__(f"{app}/{action} failed ({code}){': ' + detail if detail else ''}")


class ActionRunner(Protocol):
    """Runs one integration action deterministically (no agent) and returns its output."""

    def run(self, app: str, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...
