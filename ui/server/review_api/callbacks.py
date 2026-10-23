"""AMP callback URLs: they let anyone resume the flow, so they are stored encrypted and never logged."""

from __future__ import annotations

import httpx
from cryptography.fernet import Fernet, InvalidToken

SOURCE = "fde-review-ui"


class CallbackError(RuntimeError):
    pass


class CallbackVault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def seal(self, url: str) -> str:
        return self._fernet.encrypt(url.encode()).decode()

    def open(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as error:
            raise CallbackError("the stored callback cannot be read with this CALLBACK_ENCRYPTION_KEY") from error

    def answer(self, ciphertext: str, feedback: str) -> None:
        """POST the feedback to the paused flow's callback."""
        try:
            response = httpx.post(self.open(ciphertext), json={"feedback": feedback, "source": SOURCE}, timeout=30)
        except httpx.TransportError as error:
            raise CallbackError(f"AMP did not answer ({type(error).__name__})") from error
        if not response.is_success:
            raise CallbackError(f"AMP answered HTTP {response.status_code}")
