"""FDE profiles as YAML files shipped inside the package (src/daily_log/profiles/<fde_id>.yaml)."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

import yaml

EXAMPLE = "example.yaml"


class YamlFdeProfileRepository:
    def __init__(self, package: str = "daily_log.profiles") -> None:
        self._root = files(package)

    def get(self, fde_id: str) -> dict[str, Any] | None:
        path = self._root / f"{fde_id}.yaml"
        if not path.is_file():
            return None
        document = self._read(path)
        if document.get("fde_id") != fde_id:
            raise ValueError(f"{fde_id}.yaml declares fde_id={document.get('fde_id')!r}; they must match")
        return document

    def find_fde_id_by_email(self, email: str) -> str | None:
        email = email.strip().lower()
        for path in self._profiles():
            document = self._read(path)
            if email in [str(value).strip().lower() for value in document.get("emails", [])]:
                return document.get("fde_id")
        return None

    def _profiles(self) -> list[Traversable]:
        return sorted(
            (path for path in self._root.iterdir() if path.name.endswith(".yaml") and path.name != EXAMPLE),
            key=lambda path: path.name,
        )

    @staticmethod
    def _read(path: Traversable) -> dict[str, Any]:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ValueError(f"{path.name} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ValueError(f"{path.name} must contain a YAML mapping")
        return document
