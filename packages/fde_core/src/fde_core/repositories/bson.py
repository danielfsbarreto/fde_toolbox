"""Model <-> document conversion. BSON has no date type, so plain dates are stored as ISO strings."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


def to_document(model: BaseModel, **extra: Any) -> dict[str, Any]:
    return {**_convert(model.model_dump(mode="python")), **extra}


def _convert(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_convert(item) for item in value]
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def day_key(day: date) -> str:
    return day.isoformat()
