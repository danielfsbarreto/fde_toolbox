"""A build's env var values, encrypted (fde_core.vault). The review app writes them; the flow only reads them."""

from __future__ import annotations

from pymongo.database import Database

from fde_core.domain.builds import Build
from fde_core.repositories.indexes import BUILD_ENV
from fde_core.vault import EnvVault


class BuildEnvRepository:
    def __init__(self, db: Database) -> None:
        self._env = db[BUILD_ENV]

    def names(self, build: Build) -> set[str]:
        """The variables that have a value (no decryption)."""
        query = {"fde_id": build.fde_id, "build_id": build.id}
        return {doc["name"] for doc in self._env.find(query, {"name": 1})}

    def values(self, build: Build, vault: EnvVault) -> dict[str, str]:
        """Every value, decrypted: for the sandbox's .env only, never for a log, a prompt or MongoDB."""
        query = {"fde_id": build.fde_id, "build_id": build.id}
        return {doc["name"]: vault.open(doc["ciphertext"]) for doc in self._env.find(query)}
