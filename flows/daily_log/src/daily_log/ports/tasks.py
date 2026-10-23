"""Task tracker port (Todoist): where the action points of a final day are sent."""

from __future__ import annotations

from typing import Protocol

from fde_core.domain.action_points import NewTask, OpenTask, TaskProject, TaskSection


class TaskTracker(Protocol):
    def projects(self) -> list[TaskProject]: ...
    def sections(self) -> list[TaskSection]: ...
    def create_task(self, task: NewTask) -> str:
        """Creates the task and returns its id."""
        ...

    def open_tasks(self, project_id: str, section_id: str | None = None) -> list[OpenTask]:
        """The open tasks of a project (or of one of its sections), whoever made them."""
        ...

    def add_comment(self, task_id: str, content: str) -> str:
        """Comments on a task and returns the comment's id."""
        ...
