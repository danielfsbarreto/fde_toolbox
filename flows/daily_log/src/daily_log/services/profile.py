from __future__ import annotations

from pydantic import ValidationError

from daily_log.ports.repositories import FdeProfileRepository
from fde_core.domain.profile import FdeProfile


class ProfileError(RuntimeError):
    """The FDE profile is missing or invalid. The run stops before collecting anything."""


class ProfileService:
    def __init__(self, profiles: FdeProfileRepository) -> None:
        self._profiles = profiles

    def load(self, fde_id: str) -> FdeProfile:
        try:
            document = self._profiles.get(fde_id)
        except ValueError as error:
            raise ProfileError(f"Invalid FDE profile {fde_id!r}: {error}") from error
        if document is None:
            raise ProfileError(
                f"No FDE profile for fde_id={fde_id!r}. Add src/daily_log/profiles/{fde_id}.yaml "
                f"(start from example.yaml) and redeploy."
            )
        try:
            return FdeProfile.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
            )
            raise ProfileError(f"Invalid FDE profile {fde_id!r}: {problems}") from error

    def fde_id_for_email(self, email: str) -> str | None:
        return self._profiles.find_fde_id_by_email(email)
