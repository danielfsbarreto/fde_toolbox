from __future__ import annotations

from typing import Protocol

from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import CollectionResult, Source


class Collector(Protocol):
    """Collects one source for one day. Never raises: failures come back as a failed CollectionResult."""

    source: Source

    def collect(self, ctx: CollectionContext) -> CollectionResult: ...
