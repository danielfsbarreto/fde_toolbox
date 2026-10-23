"""Error descriptions that are safe to store and log: no payload values, no message content."""

from __future__ import annotations

from pydantic import ValidationError


def describe_error(error: BaseException, limit: int = 300) -> str:
    if isinstance(error, ValidationError):
        locations = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        return f"ValidationError at {', '.join(locations)}"[:limit]
    message = str(error).splitlines()[0] if str(error) else ""
    return f"{type(error).__name__}: {message}"[:limit]
