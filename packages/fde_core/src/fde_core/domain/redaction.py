"""Secret redaction rules.

Applied before anything is stored and before any LLM call. Rules mask credentials, not personal data.
"""

from __future__ import annotations

import math
import re
from collections import Counter

MASK = "[REDACTED]"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]+:[^\s/@]+@[^\s]+", re.I)),
    ("bearer", re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("openai_anthropic", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}")),
    ("slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("linear", re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{20,}")),
    ("granola", re.compile(r"\bgrn_[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}")),
    ("crewai", re.compile(r"\bpk_[A-Za-z0-9]{20,}")),
    (
        "assignment",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
            r"(\s*[:=]\s*|\"\s*:\s*\")([^\s\"',;]{4,})"
        ),
    ),
)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_URL = re.compile(r"https?://[^\s<>|)\]]+")
_URL_SECRET = re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|key|secret|sig|signature|password)=)[^&\s]+")


def _entropy(token: str) -> float:
    counts = Counter(token)
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())


def _looks_random(token: str) -> bool:
    classes = sum(bool(re.search(pattern, token)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    return classes >= 3 and _entropy(token) >= 4.0


def redact(text: str) -> tuple[str, int]:
    """Return the text with secrets masked and the number of masks applied."""
    if not text:
        return text, 0
    count = 0
    for name, pattern in _PATTERNS:
        if name == "assignment":
            text, n = pattern.subn(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
        else:
            text, n = pattern.subn(MASK, text)
        count += n

    def _mask_random(match: re.Match[str]) -> str:
        nonlocal count
        token = match.group(0)
        if _looks_random(token):
            count += 1
            return MASK
        return token

    # Random-looking strings are masked outside URLs only: ids in links (Slack, Gmail, Calendar) look random too.
    # Secrets inside URLs are caught by the credential and query-parameter patterns.
    text, n = _URL_SECRET.subn(lambda m: m[1] + MASK, text)
    count += n
    parts = _URL.split(text)
    urls = _URL.findall(text)
    rebuilt = [_CANDIDATE.sub(_mask_random, part) for part in parts]
    return "".join(piece for pair in zip(rebuilt, [*urls, ""], strict=True) for piece in pair), count
