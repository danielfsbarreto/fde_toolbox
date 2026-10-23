"""Builds' env var values (API keys, IDs) are stored encrypted; this app only encrypts them (fde_core.vault decrypts
them in the flow, with the same BUILD_ENV_KEY) and never returns or logs a value."""

from __future__ import annotations

from cryptography.fernet import Fernet


class EnvVault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def seal(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()
