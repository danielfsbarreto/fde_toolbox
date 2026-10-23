"""Text helpers shared by matching, dedupe and truncation."""

from __future__ import annotations

import re
import unicodedata

_SPACE = re.compile(r"\s+")


def fold(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SPACE.sub(" ", stripped.casefold()).strip()


def contains_term(text: str, term: str) -> bool:
    """Whole-word, accent- and case-insensitive match."""
    folded_term = fold(term)
    if not folded_term:
        return False
    return re.search(rf"(?<!\w){re.escape(folded_term)}(?!\w)", fold(text)) is not None


_SEPARATORS = re.compile(r"[-_./:#]+")


def names_term(place: str, term: str) -> bool:
    return contains_term(_SEPARATORS.sub(" ", place), _SEPARATORS.sub(" ", term))


_REPLY_HEADER = re.compile(
    r"^(On .+ wrote:|Em .+ escreveu:|El .+ escribi[oó]:"
    r"|-{2,} ?(Original Message|Mensagem original|Mensaje original) ?-{2,}"
    r"|From: .+|De: .+)\s*$",
    re.I,
)


def strip_quoted_reply(text: str) -> str:
    """Keep only what is new in an email: drop quoted lines and everything after a reply header."""
    kept = []
    for line in text.splitlines():
        if _REPLY_HEADER.match(line.strip()):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower().rstrip(">")
