"""Reference data resolved from the sources and cached: links between owned accounts and source ids."""

from __future__ import annotations

from pydantic import BaseModel


class AccountRefs(BaseModel):
    key: str
    slack_channel_ids: list[str] = []
    teams_channels: list[str] = []
    linear_customer_ids: list[str] = []


class ReferenceData(BaseModel):
    accounts: dict[str, AccountRefs] = {}
    internal_slack_channel_ids: list[str] = []
    warnings: list[str] = []
