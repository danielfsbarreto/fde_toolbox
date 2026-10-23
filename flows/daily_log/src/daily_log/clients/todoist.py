"""Todoist API v1 client (the FDE's own API token, from TODOIST_API_KEY).

Lists are paginated with a cursor. Rate limits (429), server errors and network failures retry with exponential
backoff and jitter; other errors raise at once.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from fde_core.domain.action_points import NewTask, OpenTask, TaskProject, TaskSection

BASE_URL = "https://api.todoist.com/api/v1"


class TodoistError(RuntimeError):
    pass


class TodoistClient:
    def __init__(self, token: str, attempts: int = 4, backoff_s: float = 2.0, timeout_s: float = 30.0) -> None:
        self._http = httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=timeout_s)
        self._attempts = attempts
        self._backoff_s = backoff_s

    def projects(self) -> list[TaskProject]:
        return [
            TaskProject(id=item["id"], name=item["name"], inbox=bool(item.get("inbox_project")))
            for item in self._list("projects")
        ]

    def sections(self) -> list[TaskSection]:
        return [
            TaskSection(id=item["id"], project_id=item["project_id"], name=item["name"])
            for item in self._list("sections")
        ]

    def create_task(self, task: NewTask) -> str:
        body = {
            "content": task.content,
            "description": task.description,
            "project_id": task.project_id,
            "labels": task.labels,
            "due_date": task.due_date.isoformat(),
        }
        if task.section_id:
            body["section_id"] = task.section_id
        return self._request("POST", "tasks", json=body)["id"]

    def open_tasks(self, project_id: str, section_id: str | None = None) -> list[OpenTask]:
        params = {"project_id": project_id, **({"section_id": section_id} if section_id else {})}
        return [
            OpenTask(
                id=item["id"],
                content=item.get("content", ""),
                description=item.get("description") or "",
                project_id=item["project_id"],
                section_id=item.get("section_id"),
                due_date=(item.get("due") or {}).get("date"),
            )
            for item in self._list("tasks", params)
            if not item.get("checked") and (section_id is None or item.get("section_id") == section_id)
        ]

    def add_comment(self, task_id: str, content: str) -> str:
        return self._request("POST", "comments", json={"task_id": task_id, "content": content})["id"]

    def _list(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = self._request(
                "GET", path, params={**(params or {}), "limit": 200, **({"cursor": cursor} if cursor else {})}
            )
            items += page.get("results", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return items

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as error:
                failure = f"{method} {path}: {type(error).__name__}"
            else:
                if response.is_success:
                    return response.json()
                failure = f"{method} {path}: HTTP {response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    raise TodoistError(failure)
            if attempt == self._attempts:
                raise TodoistError(failure)
            base = self._backoff_s * 2 ** (attempt - 1)
            time.sleep(base + random.uniform(0, base / 2))
        raise TodoistError(f"{method} {path}: no attempts")
