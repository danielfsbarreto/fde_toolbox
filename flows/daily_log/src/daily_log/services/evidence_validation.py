"""Deterministic evidence check: every topic cites at least one event, and only events it was given."""

from __future__ import annotations

from fde_core.domain.topics import TopicDraft


class EvidenceValidationService:
    def problems(self, drafts: list[TopicDraft], allowed_refs: set[str]) -> dict[int, str]:
        """Topic index -> what is wrong with its evidence. Empty when every topic is supported."""
        problems: dict[int, str] = {}
        for index, draft in enumerate(drafts):
            unknown = [ref for ref in draft.evidence if ref not in allowed_refs]
            known = [ref for ref in draft.evidence if ref in allowed_refs]
            if not known:
                problems[index] = f'topic "{draft.title}" cites no listed event'
            elif unknown:
                problems[index] = f'topic "{draft.title}" cites refs that were not listed: {", ".join(unknown[:5])}'
        return problems
