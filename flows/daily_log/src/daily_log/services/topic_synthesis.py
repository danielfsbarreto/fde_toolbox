"""Writes each account's topics and headline from its grouped events, then checks the evidence.

A section whose topics cite missing or unknown events is regenerated once with the problems as feedback; topics
still failing are kept, flagged, with only their valid citations.
"""

from __future__ import annotations

from dataclasses import dataclass

from daily_log.ports.llm import TopicSynthesizer
from daily_log.services.evidence_validation import EvidenceValidationService
from daily_log.services.topic_grouping import EventRefs
from fde_core.domain.events import INTERNAL, UNASSIGNED
from fde_core.domain.learnings import WRITING, Guidance
from fde_core.domain.profile import FdeProfile
from fde_core.domain.text import fold
from fde_core.domain.topics import SynthesisRequest, Topic, TopicDraft, TopicGroup

_SECTION_NAMES = {INTERNAL: "Internal (CrewAI work with no customer)", UNASSIGNED: "Unassigned"}


def _without_em_dashes(value):
    """House style: no em-dashes in generated text, even when a source title carries one."""
    if isinstance(value, str):
        return value.replace(" — ", ", ").replace("—", "-")
    if isinstance(value, list):
        return [_without_em_dashes(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_em_dashes(item) for key, item in value.items()}
    return value


@dataclass
class WrittenSection:
    headline: str
    topics: list[Topic]


class TopicSynthesisService:
    def __init__(self, synthesizer: TopicSynthesizer, validation: EvidenceValidationService) -> None:
        self._synthesizer = synthesizer
        self._validation = validation

    def write(
        self,
        groups: list[TopicGroup],
        refs: EventRefs,
        profile: FdeProfile,
        guidance: Guidance | None = None,
    ) -> tuple[dict[str, WrittenSection], int]:
        """Write every account that has topic groups."""
        sections: dict[str, WrittenSection] = {}
        tokens = 0
        for key in dict.fromkeys(group.account_key for group in groups):
            section_groups = [group for group in groups if group.account_key == key]
            account = profile.account(key)
            request = SynthesisRequest(
                account_key=key,
                account_name=account.name if account else _SECTION_NAMES.get(key, key),
                account_description=account.description if account else "",
                fde_name=profile.name,
                output_language=profile.output_language,
                groups=section_groups,
                events=[refs.digests[ref] for group in section_groups for ref in group.event_refs],
                guidance=(guidance or Guidance()).text(WRITING, [key]),
            )
            allowed = {ref for group in section_groups for ref in group.event_refs}
            result = self._synthesizer.synthesize(request)
            tokens += result.total_tokens
            output = result.output
            problems = self._validation.problems(output.topics, allowed)
            if problems or not output.topics:
                feedback = "; ".join(problems.values()) or "no topics were written"
                retry = self._synthesizer.synthesize(request.model_copy(update={"feedback": feedback}))
                tokens += retry.total_tokens
                output = retry.output if retry.output.topics else output
                problems = self._validation.problems(output.topics, allowed)
            sections[key] = WrittenSection(
                headline=_without_em_dashes(output.headline),
                topics=[
                    self._topic(draft, key, section_groups, refs, allowed, problems.get(index))
                    for index, draft in enumerate(output.topics)
                ],
            )
        return sections, tokens

    @staticmethod
    def _topic(
        draft: TopicDraft,
        key: str,
        groups: list[TopicGroup],
        refs: EventRefs,
        allowed: set[str],
        problem: str | None,
    ) -> Topic:
        cited = [ref for ref in draft.evidence if ref in allowed]
        group = next((group for group in groups if fold(group.title) == fold(draft.title)), None) or next(
            (group for group in groups if set(cited) & set(group.event_refs)), None
        )
        by_content = bool(group and group.reason)
        return Topic(
            **_without_em_dashes(draft.model_dump(exclude={"evidence"})),
            evidence=[refs.ids[ref] for ref in cited],
            account_key=key,
            attribution="content" if by_content else "rules",
            attribution_confidence=group.confidence if by_content and group else None,
            attribution_reason=group.reason if by_content and group else "",
            flagged=problem is not None,
            flag_reason=problem,
        )
