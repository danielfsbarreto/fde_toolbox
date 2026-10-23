"""The FDE's approved learnings for a run, as the guidance each LLM step reads (fde_core.domain.learnings)."""

from __future__ import annotations

from daily_log.ports.repositories import LearningRepository
from fde_core.domain.events import INTERNAL, UNASSIGNED
from fde_core.domain.learnings import Guidance
from fde_core.domain.profile import FdeProfile


class GuidanceService:
    def __init__(self, learnings: LearningRepository) -> None:
        self._learnings = learnings

    def load(self, profile: FdeProfile) -> Guidance:
        names = {account.key: account.name for account in profile.owned_accounts}
        names |= {INTERNAL: "Internal", UNASSIGNED: "Unassigned"}
        return Guidance(learnings=self._learnings.approved(profile.fde_id), account_names=names)
