"""Direct edits on the stored day: every change to a single item. No LLM.

Each write names the day's version it was made on (a stale write is refused), and nothing can be edited while
the agent works on review requests (status "working").
"""

from __future__ import annotations

from typing import Any

from review_api.contract import INTERNAL, MANUAL, UNASSIGNED
from review_api.store import StaleError, Store, new_id

WORKSTREAMS = {
    "pre-sales",
    "poc",
    "enablement",
    "support",
    "delivery",
    "internal-initiative",
    "hiring",
    "events",
    "other",
}
TOPIC_FIELDS = {
    "title",
    "workstream",
    "what_happened",
    "status_or_decision",
    "risks",
    "people",
    "my_involvement",
    "confidence",
    "reviewed",
}
FOLLOW_UP_FIELDS = {"what", "owner", "due_if_stated", "reviewed"}
ENTRY_FIELDS = {"account_key", "title", "what", "workstream", "time", "duration_min", "people", "follow_ups"}


class EditError(ValueError):
    """The edit names something that does not exist or is not allowed."""


class LockedError(RuntimeError):
    """The agent is working on the day."""


class FinalError(RuntimeError):
    """The day is final: the record of what happened. It is not edited; it is deleted and run again."""


def accounts(log: dict[str, Any]) -> dict[str, str]:
    names = {digest["account_key"]: digest["account_name"] for digest in log.get("accounts", [])}
    return names | {INTERNAL: "Internal", UNASSIGNED: "Unassigned"}


def _lists(log: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    lists = {digest["account_key"]: digest.setdefault("topics", []) for digest in log.get("accounts", [])}
    lists[INTERNAL] = log.setdefault("internal", [])
    lists[UNASSIGNED] = log.setdefault("unassigned", [])
    return lists


def _clean(patch: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(patch) - allowed
    if unknown:
        raise EditError(f"cannot change {', '.join(sorted(unknown))}")
    if "workstream" in patch and patch["workstream"] not in WORKSTREAMS:
        raise EditError(f"unknown workstream {patch['workstream']!r}")
    return patch


class Edits:
    def __init__(self, store: Store) -> None:
        self._store = store

    # Topics ------------------------------------------------------------------------------------------

    def update_topic(self, day: str, version: int, topic_id: str, patch: dict[str, Any]) -> None:
        """Marking a topic reviewed, or not reviewed, does the same to all its current follow-ups."""
        log = self._open(day, version)
        _, topic = self._topic(log, topic_id)
        topic.update(_clean(patch, TOPIC_FIELDS))
        if "reviewed" in patch:
            for item in topic.get("follow_ups", []):
                item["reviewed"] = bool(patch["reviewed"])
        self._store.save_day(log, version)

    def move_topic(self, day: str, version: int, topic_id: str, account_key: str) -> None:
        log = self._open(day, version)
        lists = _lists(log)
        if account_key not in lists:
            raise EditError(f"unknown account {account_key!r}")
        source, topic = self._topic(log, topic_id)
        if topic["account_key"] == account_key:
            return
        source.remove(topic)
        topic.update(account_key=account_key, attribution="review", attribution_reason="moved in review")
        lists[account_key].append(topic)
        self._store.save_day(log, version)
        self._store.move_events(day, topic.get("evidence", []), account_key)

    def remove_topic(self, day: str, version: int, topic_id: str) -> None:
        log = self._open(day, version)
        source, topic = self._topic(log, topic_id)
        source.remove(topic)
        log.setdefault("removed", []).append(topic)
        self._store.save_day(log, version)

    def restore_topic(self, day: str, version: int, topic_id: str) -> None:
        log = self._open(day, version)
        removed = log.setdefault("removed", [])
        topic = next((item for item in removed if item.get("id") == topic_id), None)
        if topic is None:
            raise EditError("that topic is not among the removed ones")
        removed.remove(topic)
        lists = _lists(log)
        lists.get(topic["account_key"], lists[UNASSIGNED]).append(topic)
        self._store.save_day(log, version)

    def set_headline(self, day: str, version: int, account_key: str, text: str) -> None:
        log = self._open(day, version)
        digest = next((item for item in log.get("accounts", []) if item["account_key"] == account_key), None)
        if digest is None:
            raise EditError(f"no account section {account_key!r}")
        digest["headline"] = text.strip()
        self._store.save_day(log, version)

    # Follow-ups --------------------------------------------------------------------------------------

    def add_follow_up(self, day: str, version: int, topic_id: str, patch: dict[str, Any]) -> None:
        fields = _clean(patch, FOLLOW_UP_FIELDS)
        if not str(fields.get("what", "")).strip():
            raise EditError("a follow-up needs a text")
        log = self._open(day, version)
        _, topic = self._topic(log, topic_id)
        topic.setdefault("follow_ups", []).append(
            {"id": new_id(), "reviewed": False, "owner": None, "due_if_stated": None, **fields}
        )
        self._store.save_day(log, version)

    def update_follow_up(self, day: str, version: int, topic_id: str, follow_up_id: str, patch: dict) -> None:
        log = self._open(day, version)
        _, topic = self._topic(log, topic_id)
        self._follow_up(topic, follow_up_id).update(_clean(patch, FOLLOW_UP_FIELDS))
        self._store.save_day(log, version)

    def remove_follow_up(self, day: str, version: int, topic_id: str, follow_up_id: str) -> None:
        """Kept aside on the log (like removed topics), with its place, so it can be restored as it was."""
        log = self._open(day, version)
        _, topic = self._topic(log, topic_id)
        item = self._follow_up(topic, follow_up_id)
        index = topic["follow_ups"].index(item)
        topic["follow_ups"].remove(item)
        log.setdefault("removed_follow_ups", []).append({"topic_id": topic_id, "index": index, "follow_up": item})
        self._store.save_day(log, version)

    def restore_follow_up(self, day: str, version: int, topic_id: str, follow_up_id: str) -> None:
        log = self._open(day, version)
        removed = log.setdefault("removed_follow_ups", [])
        entry = next(
            (item for item in removed if item["topic_id"] == topic_id and item["follow_up"].get("id") == follow_up_id),
            None,
        )
        if entry is None:
            raise EditError("that follow-up is not among the removed ones")
        _, topic = self._topic(log, topic_id)
        follow_ups = topic.setdefault("follow_ups", [])
        follow_ups.insert(min(int(entry.get("index", len(follow_ups))), len(follow_ups)), entry["follow_up"])
        removed.remove(entry)
        self._store.save_day(log, version)

    def move_follow_up(self, day: str, version: int, topic_id: str, follow_up_id: str, to_topic_id: str) -> None:
        log = self._open(day, version)
        _, source = self._topic(log, topic_id)
        _, target = self._topic(log, to_topic_id)
        item = self._follow_up(source, follow_up_id)
        source["follow_ups"].remove(item)
        target.setdefault("follow_ups", []).append(item)
        self._store.save_day(log, version)

    # Comments and offline additions (day_overrides is their source of truth) ---------------------------

    def set_comment(self, day: str, version: int, account_key: str, text: str) -> None:
        log = self._open(day, version)
        if account_key not in accounts(log):
            raise EditError(f"unknown account {account_key!r}")
        overrides = self._store.overrides(day)
        comments = overrides.setdefault("comments", {})
        if text.strip():
            comments[account_key] = text.strip()
        else:
            comments.pop(account_key, None)
        self._store.save_overrides(overrides)
        log["comments"] = dict(comments)  # shown right away; the flow keeps both in step
        self._store.save_day(log, version)

    def add_entry(self, day: str, version: int, entry: dict[str, Any]) -> None:
        log = self._open(day, version)
        overrides = self._store.overrides(day)
        overrides.setdefault("manual_entries", []).append({"id": new_id(), **self._entry(log, entry)})
        self._store.save_overrides(overrides)
        self._store.save_day(log, version)

    def update_entry(self, day: str, version: int, entry_id: str, entry: dict[str, Any]) -> None:
        log = self._open(day, version)
        overrides = self._store.overrides(day)
        entries = overrides.setdefault("manual_entries", [])
        index = next((i for i, item in enumerate(entries) if item.get("id") == entry_id), None)
        if index is None:
            raise EditError("no such addition")
        entries[index] = {"id": entry_id, **self._entry(log, entry)}
        self._store.save_overrides(overrides)
        self._store.save_day(log, version)

    def remove_entry(self, day: str, version: int, entry_id: str) -> None:
        log = self._open(day, version)
        overrides = self._store.overrides(day)
        entries = overrides.setdefault("manual_entries", [])
        if not any(item.get("id") == entry_id for item in entries):
            raise EditError("no such addition")
        overrides["manual_entries"] = [item for item in entries if item.get("id") != entry_id]
        self._store.save_overrides(overrides)
        self._store.save_day(log, version)

    # The flow's pause --------------------------------------------------------------------------------

    def mark_working(self, day: str) -> None:
        """Requests went to the agent: the day is locked until the flow shows it again."""
        log = self._store.day(day)
        log["review_status"] = "working"
        self._store.save_day(log, int(log.get("version", 0)))

    # Helpers -----------------------------------------------------------------------------------------

    def _open(self, day: str, version: int) -> dict[str, Any]:
        log = self._store.day(day)
        if log.get("review_status") == "final":
            raise FinalError("the day is final")
        if log.get("review_status") == "working":
            raise LockedError("the agent is working on this day")
        if int(log.get("version", 0)) != version:
            raise StaleError("the day changed meanwhile")
        return log

    @staticmethod
    def _topic(log: dict[str, Any], topic_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        for topics in _lists(log).values():
            for topic in topics:
                if topic.get("id") == topic_id and topic.get("attribution") != MANUAL:
                    return topics, topic
        raise EditError("no such topic (additions are edited as additions)")

    @staticmethod
    def _follow_up(topic: dict[str, Any], follow_up_id: str) -> dict[str, Any]:
        item = next((item for item in topic.get("follow_ups", []) if item.get("id") == follow_up_id), None)
        if item is None:
            raise EditError("no such follow-up")
        return item

    @staticmethod
    def _entry(log: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        fields = _clean({key: value for key, value in entry.items() if key != "id"}, ENTRY_FIELDS)
        if fields.get("account_key") not in accounts(log):
            raise EditError(f"unknown account {fields.get('account_key')!r}")
        if not str(fields.get("title", "")).strip() or not str(fields.get("what", "")).strip():
            raise EditError("an addition needs a title and what happened")
        fields.setdefault("workstream", "other")
        _clean({"workstream": fields["workstream"]}, {"workstream"})
        return {
            "account_key": fields["account_key"],
            "title": fields["title"].strip(),
            "what": fields["what"].strip(),
            "workstream": fields["workstream"],
            "time": fields.get("time") or None,
            "duration_min": fields.get("duration_min") or None,
            "people": [str(name).strip() for name in fields.get("people") or [] if str(name).strip()],
            "follow_ups": [
                {
                    "id": new_id(),
                    "reviewed": False,
                    "what": str(item.get("what", "")).strip(),
                    "owner": item.get("owner"),
                    "due_if_stated": item.get("due_if_stated"),
                }
                for item in fields.get("follow_ups") or []
                if str(item.get("what", "")).strip()
            ],
        }
