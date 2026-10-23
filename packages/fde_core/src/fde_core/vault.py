"""Values a build's generated project needs (API keys, IDs): stored encrypted, decrypted only for its sandbox.

Fernet, with BUILD_ENV_KEY from the environment (the review app's server and the flow share it). The review app
encrypts what you enter; the flow decrypts the values to write the sandbox's .env and scrubs them from any output.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

SCRUB_MIN = 6  # shorter values (a flag, a number) would mangle unrelated text


class VaultError(RuntimeError):
    pass


class EnvVault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def seal(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def open(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as error:
            raise VaultError("a stored value cannot be read with this BUILD_ENV_KEY") from error


def scrub(text: str, values: dict[str, str]) -> str:
    """The text with every value replaced by a marker naming it, e.g. [redacted:ANTHROPIC_API_KEY]: for any output
    the agent, a log or the app sees. The review app shows the marker as a locked chip."""
    for name, value in sorted(values.items(), key=lambda item: -len(item[1])):
        if len(value) >= SCRUB_MIN:
            text = text.replace(value, f"[redacted:{name}]")
    return text
