"""The chat message the review app sends for each turn: the user's text plus, for gate buttons, a structured action.

Chat transports carry one string per message, so the app sends JSON. A plain string (a terminal chat) is just text.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from fde_core.domain.builds import Answer, Stage

Action = Literal["approve", "request_changes", "smoke"]  # smoke: (re)open the smoke test's form


class CodeRef(BaseModel):
    """Lines the user selected in the code view, to ask about them."""

    path: str
    start: int
    end: int


class Envelope(BaseModel):
    text: str = ""
    build_id: str | None = None  # the build the message belongs to; None in a terminal chat (its session is the build)
    message_id: str | None = None  # the user message in build_messages; None in a terminal chat
    action: Action | None = None
    stage: Stage | None = None
    version: int | None = None
    ref: CodeRef | None = None
    request_id: str | None = None  # the form these answers are for
    answers: dict[str, Answer] | None = None  # by question id: the chosen option, the user's own words, or both


def parse(raw: str | None) -> Envelope:
    raw = raw or ""
    if raw.lstrip().startswith("{"):
        try:
            return Envelope.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            pass
    return Envelope(text=raw)
