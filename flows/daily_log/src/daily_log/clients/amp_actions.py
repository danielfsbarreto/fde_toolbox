"""Deterministic calls to CrewAI AMP integration actions, without an agent.

Uses crewai_tools' ClipperClient (the same client agent tools use) with the FDE's own connection,
selected by alias (default "private"). Auth comes from CREWAI_PLATFORM_INTEGRATION_TOKEN.

Failures retry with exponential backoff and jitter. AMP marks provider failures (`tool_execution_failed`)
as not retryable, but under parallel load they are mostly transient throttling, so they are retried too.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any

import requests
from crewai_tools.tools.crewai_platform_tools.integrations_client import (
    ApplicationSelector,
    ClipperClient,
    ToolExecutionSuccess,
    ToolInfo,
)

from daily_log.ports.integrations import ActionError

_TRANSIENT_CODES = {"tool_execution_failed", "rate_limited", "429", "timeout"}


class AmpActionRunner:
    def __init__(self, connection: str = "private", attempts: int = 4, backoff_s: float = 2.0) -> None:
        self._connection = connection
        self._attempts = attempts
        self._backoff_s = backoff_s
        self._client = ClipperClient()
        self._tools: dict[str, ToolInfo] = {}
        self._lock = threading.Lock()

    def run(self, app: str, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        tool = self._tool(app, action)
        for attempt in range(1, self._attempts + 1):
            try:
                result = self._client.execute_action(tool, arguments or {})
            except requests.RequestException as error:
                if attempt == self._attempts:
                    raise ActionError(app, action, "network", type(error).__name__) from error
            else:
                if isinstance(result, ToolExecutionSuccess):
                    output = result.output
                    return output if isinstance(output, dict) else {"data": output}
                transient = result.retryable or str(result.code) in _TRANSIENT_CODES
                if not transient or attempt == self._attempts:
                    raise ActionError(app, action, result.code, result.message[:200])
            time.sleep(self._delay(attempt))
        raise AssertionError("unreachable")

    def _delay(self, attempt: int) -> float:
        """Exponential backoff (1x, 2x, 4x the base) with up to 50% random jitter, so parallel calls spread out."""
        base = self._backoff_s * 2 ** (attempt - 1)
        return base + random.uniform(0, base / 2)

    def _tool(self, app: str, action: str) -> ToolInfo:
        key = f"{app}/{action}"
        with self._lock:
            if key not in self._tools:
                selector = ApplicationSelector.from_string(f"{key}@{self._connection}")
                try:
                    [tool] = self._client.get_actions([selector])
                except requests.HTTPError as error:
                    status = error.response.status_code if error.response is not None else "http"
                    raise ActionError(app, action, str(status), "action not available") from error
                self._tools[key] = tool
            return self._tools[key]
