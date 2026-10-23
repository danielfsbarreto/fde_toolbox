"""AMP's Human in the Loop webhook: verify its signature, and read which paused run it is about.

AMP's docs describe X-Signature: sha256=<hex HMAC-SHA256 of "{X-Timestamp}.{body}">; AMP actually sends the
X-CrewAI-Signature and X-CrewAI-Timestamp headers, and both are read. Secrets come as "whsec_…",
which some senders use as raw text and others base64-decode first (the Standard Webhooks / Svix scheme, with
webhook-id/-timestamp/-signature headers and "v1,<base64>" signatures). All of those are accepted, each with a
constant-time comparison and a 5-minute freshness window; the variant that matched (or why none did) is logged
without any secret, so the real scheme shows up in the logs.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Mapping

MAX_AGE_S = 300


def _keys(secret: str) -> list[tuple[str, bytes]]:
    keys = [("raw", secret.encode())]
    bare = secret.removeprefix("whsec_")
    if bare != secret:
        keys.append(("without-prefix", bare.encode()))
    try:
        keys.append(("base64", base64.b64decode(bare, validate=True)))
    except (binascii.Error, ValueError):
        pass
    return keys


def _seconds(timestamp: str) -> float | None:
    try:
        value = float(timestamp)
    except ValueError:
        return None
    return value / 1000 if value > 1e11 else value  # milliseconds


def _signatures(header: str) -> list[str]:
    """Candidate signatures in a header: "sha256=<hex>", "<hex>", or space-separated "v1,<base64>" entries."""
    found = []
    for part in header.replace(",", " ").split():
        if part in ("v1", "v1a"):
            continue
        found.append(part.removeprefix("sha256=").removeprefix("v1="))
    return found


def verify(secret: str, body: bytes, headers: Mapping[str, str], now: float | None = None) -> tuple[bool, str]:
    """(valid, how): which scheme matched, or why none did."""
    lower = {key.lower(): value for key, value in headers.items()}
    schemes = []
    for prefix in ("x-crewai", "x"):  # AMP sends X-CrewAI-Signature / X-CrewAI-Timestamp; its docs say X-Signature
        signature, stamp = lower.get(f"{prefix}-signature"), lower.get(f"{prefix}-timestamp")
        if signature and stamp:
            schemes.append((f"{prefix}-signature", stamp, signature, f"{stamp}.".encode()))
    for prefix in ("webhook", "svix"):
        if lower.get(f"{prefix}-signature") and lower.get(f"{prefix}-timestamp") and lower.get(f"{prefix}-id"):
            stamp = lower[f"{prefix}-timestamp"]
            schemes.append((prefix, stamp, lower[f"{prefix}-signature"], f"{lower[f'{prefix}-id']}.{stamp}.".encode()))
    if not schemes:
        return False, f"no signature headers (got: {', '.join(sorted(lower)) or 'none'})"

    reasons = []
    for scheme, stamp, header, prefix in schemes:
        sent = _seconds(stamp)
        if sent is None:
            reasons.append(f"{scheme}: timestamp {stamp[:16]!r} unreadable")
            continue
        age = abs((now or time.time()) - sent)
        if age > MAX_AGE_S:
            reasons.append(f"{scheme}: timestamp {int(age)} s old")
            continue
        candidates = _signatures(header)
        for key_name, key in _keys(secret):
            digest = hmac.new(key, prefix + body, hashlib.sha256).digest()
            for encoded, name in ((digest.hex(), "hex"), (base64.b64encode(digest).decode(), "base64")):
                if any(hmac.compare_digest(encoded, candidate) for candidate in candidates):
                    return True, f"{scheme}, {key_name} key, {name} signature"
        shapes = ", ".join(f"{len(candidate)} chars" for candidate in candidates[:3])
        reasons.append(f"{scheme}: no key matched (signature {shapes})")
    return False, "; ".join(reasons)


def run_reference(payload: dict) -> tuple[str | None, str | None, str | None]:
    """(run id, fde id, date) of the paused run, from the flow state AMP sends.

    AMP sends the state at the top level; its docs put it under "request".
    """
    state = payload.get("state") or (payload.get("request") or {}).get("state") or {}
    run = state.get("run") or {}
    return run.get("run_id"), run.get("fde_id") or state.get("fde_id"), run.get("day")


def shape(value: object, depth: int = 0) -> str:
    """The payload's key structure, without any values, for the logs (AMP's real payload has differed from its docs)."""
    if isinstance(value, dict) and depth < 3:
        return "{" + ", ".join(f"{key}: {shape(child, depth + 1)}" for key, child in list(value.items())[:25]) + "}"
    if isinstance(value, dict):
        return "{…}"
    if isinstance(value, list):
        return f"[{len(value)}]"
    return type(value).__name__
