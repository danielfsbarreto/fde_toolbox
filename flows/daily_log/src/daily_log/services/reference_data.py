"""Links owned accounts to source ids (Slack channels, Teams channels, Linear customers).

Linear customers are listed once and cached; an owned account is linked to every customer whose domains, or
whose name, match the account's email domains, name or aliases. The profile's `linear_customer_ids` override this.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_log.ports.integrations import ActionError, ActionRunner
from daily_log.ports.repositories import ReferenceCacheRepository
from fde_core.domain.profile import FdeProfile, OwnedAccount
from fde_core.domain.reference import AccountRefs, ReferenceData
from fde_core.domain.text import fold

log = logging.getLogger(__name__)

_SLACK_ID = re.compile(r"^[CGD][A-Z0-9]{8,}$")
_LINEAR_CUSTOMERS = "linear_customers"
_CUSTOMERS_QUERY = """query($after: String) {
  customers(first: 100, after: $after) { nodes { id name domains } pageInfo { hasNextPage endCursor } }
}"""


def _links(account: OwnedAccount, customer: dict[str, Any]) -> bool:
    domains = {domain.lower() for domain in customer.get("domains") or []}
    name = (customer.get("name") or "").strip().lower()
    own = set(account.email_domains)
    if domains & own or name in own:
        return True
    return fold(customer.get("name") or "") in {fold(term) for term in [account.name, *account.aliases]}


class ReferenceDataService:
    def __init__(
        self,
        actions: ActionRunner,
        cache: ReferenceCacheRepository,
        cache_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._actions = actions
        self._cache = cache
        self._max_age = timedelta(days=cache_days)
        self._clock = clock

    def resolve(self, profile: FdeProfile) -> ReferenceData:
        references = ReferenceData()
        customers = self._linear_customers(profile, references) if profile.sources.linear.enabled else []
        for account in profile.owned_accounts:
            slack_ids, unresolved = self._slack_ids(account.slack_channels)
            references.warnings += [f"{account.key}: Slack channel {name!r} is not an id" for name in unresolved]
            linear_ids = list(account.linear_customer_ids) or [
                customer["id"] for customer in customers if _links(account, customer)
            ]
            if profile.sources.linear.enabled and customers and not linear_ids:
                references.warnings.append(f"{account.name}: no Linear customer linked (set linear_customer_ids)")
            references.accounts[account.key] = AccountRefs(
                key=account.key,
                slack_channel_ids=slack_ids,
                teams_channels=list(account.teams_channels),
                linear_customer_ids=linear_ids,
            )
        internal_ids, unresolved = self._slack_ids(profile.internal.slack_channels)
        references.internal_slack_channel_ids = internal_ids
        references.warnings += [f"internal: Slack channel {name!r} is not an id" for name in unresolved]
        return references

    def _linear_customers(self, profile: FdeProfile, references: ReferenceData) -> list[dict[str, Any]]:
        cached = self._cache.get(profile.fde_id, _LINEAR_CUSTOMERS)
        if cached and self._clock() - cached[1] < self._max_age:
            return list(cached[0])
        customers: list[dict[str, Any]] = []
        after = None
        try:
            for _ in range(20):
                output = self._actions.run(
                    "linear",
                    "run_query_or_mutation",
                    {"query_or_mutation": _CUSTOMERS_QUERY, "variables": {"after": after}},
                )
                page = (output.get("data") or {}).get("customers") or {}
                customers += page.get("nodes", [])
                if not (page.get("pageInfo") or {}).get("hasNextPage"):
                    break
                after = page["pageInfo"]["endCursor"]
        except ActionError as error:
            log.warning("Linear customers unavailable: %s", error)
            references.warnings.append("Linear customers could not be listed; using the cached list if any")
            return list(cached[0]) if cached else []
        self._cache.put(profile.fde_id, _LINEAR_CUSTOMERS, customers)
        return customers

    @staticmethod
    def _slack_ids(channels: list[str]) -> tuple[list[str], list[str]]:
        ids = [channel for channel in channels if _SLACK_ID.match(channel)]
        return ids, [channel for channel in channels if channel not in ids]
