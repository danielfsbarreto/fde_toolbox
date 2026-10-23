"""MongoDB access, on the documents described in contract.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.server_api import ServerApi

BUILD_STAGES = ("spec", "design", "code", "test")


class StaleError(RuntimeError):
    """The day changed since it was read: read it again."""


def new_id() -> str:
    return uuid4().hex[:10]


def all_topics(log: dict[str, Any]) -> list[dict[str, Any]]:
    topics = [topic for digest in log.get("accounts", []) for topic in digest.get("topics", [])]
    return topics + log.get("internal", []) + log.get("unassigned", []) + log.get("removed", [])


def _give_ids(log: dict[str, Any]) -> bool:
    """Days stored before item ids existed get them (and keep them: the caller saves)."""
    changed = False
    for topic in all_topics(log):
        if not topic.get("id"):
            topic["id"], changed = new_id(), True
        for item in topic.get("follow_ups", []):
            if not item.get("id"):
                item["id"], changed = new_id(), True
    return changed


def connect(uri: str, database: str) -> Database:
    client: MongoClient = MongoClient(
        uri, server_api=ServerApi("1"), tz_aware=True, appname="fde-review-api", serverSelectionTimeoutMS=10_000
    )
    return client[database]


class Store:
    def __init__(self, db: Database, fde_id: str) -> None:
        self._db = db
        self.fde_id = fde_id

    # Days ----------------------------------------------------------------------------------------------

    def days(self, limit: int = 30) -> list[dict[str, Any]]:
        fields = {"_id": 0, "date": 1, "review_status": 1, "reviewed_at": 1}
        # Only what the list's progress needs: each topic's reviewed mark and attribution
        for path in ("accounts.topics", "internal", "unassigned"):
            fields |= {f"{path}.reviewed": 1, f"{path}.attribution": 1}
        return list(self._db.daily_logs.find({"fde_id": self.fde_id}, fields).sort("date", DESCENDING).limit(limit))

    def day(self, day: str) -> dict[str, Any]:
        log = self._db.daily_logs.find_one({"fde_id": self.fde_id, "date": day}, {"_id": 0})
        if log is None:
            raise LookupError(f"no daily log for {day}")
        if _give_ids(log):
            self.save_day(log, int(log.get("version", 0)))
        return log

    def save_day(self, log: dict[str, Any], expected_version: int) -> None:
        current: dict[str, Any] = {"version": expected_version}
        if expected_version == 0:
            current = {"$or": [current, {"version": {"$exists": False}}]}
        log["version"] = expected_version + 1
        query = {"fde_id": self.fde_id, "date": log["date"], **current}
        if self._db.daily_logs.replace_one(query, log).matched_count == 0:
            log["version"] = expected_version
            raise StaleError("the day changed meanwhile")

    # Overrides (manual entries and comments) -----------------------------------------------------------

    def tasks(self, day: str) -> dict[str, dict[str, Any]]:
        """The Todoist tasks the day's follow-ups created, by follow-up id (written by the flow's sync)."""
        found = self._db.action_items.find(
            {"fde_id": self.fde_id, "date": day, "follow_up_ids.0": {"$exists": True}},
            {"_id": 0, "task_id": 1, "target": 1, "due_date": 1, "follow_up_ids": 1, "action": 1, "title": 1},
        )
        return {follow_up_id: item for item in found for follow_up_id in item["follow_up_ids"]}

    # Learnings ----------------------------------------------------------------------------------------

    def learnings(self) -> list[dict[str, Any]]:
        return list(self._db.learnings.find({"fde_id": self.fde_id}, {"_id": 0}).sort("created_at", ASCENDING))

    def add_learning(self, scope: str, steps: list[str], text: str, status: str = "approved") -> dict[str, Any]:
        """A learning written by hand: approved at once, or left proposed to decide later."""
        now = datetime.now(UTC)
        learning = {
            "id": new_id(),
            "fde_id": self.fde_id,
            "scope": scope,
            "steps": steps,
            "text": text,
            "status": status,
            "source": "manual",
            "evidence": [],
            "created_at": now,
            "updated_at": now,
            "decided_at": now if status == "approved" else None,
        }
        self._db.learnings.insert_one(dict(learning))
        return learning

    def update_learning(self, learning_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        fields = {**changes, "updated_at": now} | ({"decided_at": now} if "status" in changes else {})
        self._db.learnings.update_one({"fde_id": self.fde_id, "id": learning_id}, {"$set": fields})
        return self._db.learnings.find_one({"fde_id": self.fde_id, "id": learning_id}, {"_id": 0})

    # Reports --------------------------------------------------------------------------------------------

    def reports(self, limit: int = 50) -> list[dict[str, Any]]:
        fields = {"_id": 0, "content.sections": 0}
        return list(self._db.reports.find({"fde_id": self.fde_id}, fields).sort("created_at", DESCENDING).limit(limit))

    def report(self, report_id: str) -> dict[str, Any] | None:
        return self._db.reports.find_one({"fde_id": self.fde_id, "id": report_id}, {"_id": 0})

    def add_report(self, request: dict[str, Any]) -> dict[str, Any]:
        report = {
            "id": new_id(),
            "fde_id": self.fde_id,
            "request": request,
            "status": "queued",
            "error": None,
            "kickoff_id": None,
            "content": None,
            "tokens": 0,
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
        }
        self._db.reports.insert_one(dict(report))
        return report

    def update_report(self, report_id: str, fields: dict[str, Any]) -> None:
        self._db.reports.update_one({"fde_id": self.fde_id, "id": report_id}, {"$set": fields})

    def delete_report(self, report_id: str) -> int:
        return self._db.reports.delete_one({"fde_id": self.fde_id, "id": report_id}).deleted_count

    # Builds (the project builder) ---------------------------------------------------------------------------

    def builds(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(
            self._db.builds.find({"fde_id": self.fde_id}, {"_id": 0}).sort("updated_at", DESCENDING).limit(limit)
        )

    def build(self, build_id: str) -> dict[str, Any] | None:
        return self._db.builds.find_one({"fde_id": self.fde_id, "id": build_id}, {"_id": 0})

    def add_build(self, build_id: str, title: str, account: str | None, session_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        build = {
            "id": build_id,
            "fde_id": self.fde_id,
            "title": title,
            "account": account,
            "session_id": session_id,
            "stages": {
                stage: {"status": "not_started", "version": 0, "approved_version": None} for stage in BUILD_STAGES
            },
            "current": "spec",
            "turn": None,
            "pending_input": None,
            "sandbox_id": None,
            "seq": 0,
            "created_at": now,
            "updated_at": now,
        }
        self._db.builds.insert_one(dict(build))
        return build

    def set_session(self, build_id: str, session_id: str) -> None:
        """The build's AMP chat session (a new one when AMP lost the old one)."""
        self._db.builds.update_one({"fde_id": self.fde_id, "id": build_id}, {"$set": {"session_id": session_id}})

    def rename_build(self, build_id: str, title: str) -> None:
        self._db.builds.update_one(
            {"fde_id": self.fde_id, "id": build_id}, {"$set": {"title": title, "updated_at": datetime.now(UTC)}}
        )

    def delete_build(self, build: dict[str, Any]) -> None:
        for collection in ("build_messages", "build_activity", "build_artifacts", "build_env"):
            self._db[collection].delete_many({"fde_id": self.fde_id, "build_id": build["id"]})
        self._db.builds.delete_one({"fde_id": self.fde_id, "id": build["id"]})

    def add_user_message(
        self,
        build_id: str,
        text: str,
        envelope: dict[str, Any],
        delivery: str = "queued",
        answers_for: str | None = None,
    ) -> dict[str, Any]:
        """A user message, queued for its turn. The envelope (the JSON sent as the turn) carries the build's id and the
        message's, which is how the flow finds both."""
        counted = self._db.builds.find_one_and_update(
            {"fde_id": self.fde_id, "id": build_id},
            {"$inc": {"seq": 1}, "$set": {"updated_at": datetime.now(UTC)}},
            projection={"seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if counted is None:
            raise LookupError("no such build")
        message_id = new_id()
        message = {
            "id": message_id,
            "fde_id": self.fde_id,
            "build_id": build_id,
            "seq": counted["seq"],
            "role": "user",
            "author": "you",
            "text": text,
            "card": None,
            "turn_id": None,
            "delivery": delivery,  # "queued" only for a local worker: on AMP the app sends it itself
            "envelope": json.dumps({**envelope, "build_id": build_id, "message_id": message_id}),
            "answers_for": answers_for,  # the form whose answers it carries: the form shows them, so the UI hides this
            "created_at": datetime.now(UTC),
        }
        self._db.build_messages.insert_one(dict(message))
        return message

    def build_messages(self, build_id: str, after: int = 0) -> list[dict[str, Any]]:
        query = {"fde_id": self.fde_id, "build_id": build_id, "seq": {"$gt": after}}
        return list(self._db.build_messages.find(query, {"_id": 0, "envelope": 0}).sort("seq", ASCENDING))

    def build_message(self, build_id: str, message_id: str) -> dict[str, Any] | None:
        return self._db.build_messages.find_one(
            {"fde_id": self.fde_id, "build_id": build_id, "id": message_id}, {"_id": 0, "envelope": 0}
        )

    def build_activity(self, build_id: str, after: int = 0) -> list[dict[str, Any]]:
        query = {"fde_id": self.fde_id, "build_id": build_id, "seq": {"$gt": after}}
        return list(self._db.build_activity.find(query, {"_id": 0}).sort("seq", ASCENDING))

    def last_activity(self, build_id: str) -> dict[str, Any] | None:
        return self._db.build_activity.find_one(
            {"fde_id": self.fde_id, "build_id": build_id}, {"_id": 0}, sort=[("seq", DESCENDING)]
        )

    def artifact(self, build_id: str, stage: str, version: int | None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"fde_id": self.fde_id, "build_id": build_id, "stage": stage}
        if version is not None:
            query["version"] = version
        return self._db.build_artifacts.find_one(query, {"_id": 0}, sort=[("version", DESCENDING)])

    def artifact_versions(self, build_id: str, stage: str) -> list[dict[str, Any]]:
        fields = {"_id": 0, "version": 1, "summary": 1, "author": 1, "created_at": 1}
        query = {"fde_id": self.fde_id, "build_id": build_id, "stage": stage}
        return list(self._db.build_artifacts.find(query, fields).sort("version", DESCENDING))

    def build_env(self, build_id: str) -> list[dict[str, Any]]:
        """Which variables have a value, and since when: never the values."""
        query = {"fde_id": self.fde_id, "build_id": build_id}
        return list(self._db.build_env.find(query, {"_id": 0, "name": 1, "set_at": 1}).sort("name", ASCENDING))

    def set_build_env(self, build_id: str, name: str, ciphertext: str) -> None:
        self._db.build_env.update_one(
            {"fde_id": self.fde_id, "build_id": build_id, "name": name},
            {"$set": {"ciphertext": ciphertext, "set_at": datetime.now(UTC)}},
            upsert=True,
        )

    def delete_build_env(self, build_id: str, name: str) -> None:
        self._db.build_env.delete_one({"fde_id": self.fde_id, "build_id": build_id, "name": name})

    def reuse_build_env(self, build_id: str, name: str, from_build: str) -> bool:
        """Copy a value (still encrypted) from another of this FDE's builds; False when that build has none."""
        found = self._db.build_env.find_one({"fde_id": self.fde_id, "build_id": from_build, "name": name})
        if found is None:
            return False
        self.set_build_env(build_id, name, found["ciphertext"])
        return True

    def env_elsewhere(self, build_id: str) -> dict[str, list[dict[str, str]]]:
        """For each name this FDE set in other builds: those builds (id, title), to offer reusing a value."""
        others: dict[str, list[str]] = {}
        for doc in self._db.build_env.find(
            {"fde_id": self.fde_id, "build_id": {"$ne": build_id}}, {"name": 1, "build_id": 1}
        ):
            others.setdefault(doc["name"], []).append(doc["build_id"])
        ids = {build for builds in others.values() for build in builds}
        titles = {
            b["id"]: b.get("title", "")
            for b in self._db.builds.find({"fde_id": self.fde_id, "id": {"$in": list(ids)}}, {"id": 1, "title": 1})
        }
        return {
            name: [{"build_id": build, "title": titles[build]} for build in builds if build in titles]
            for name, builds in others.items()
        }

    def start_turn(self, build_id: str, message_id: str) -> bool:
        """Claim the build for this message's turn; False when another turn is in progress."""
        turn = {"id": message_id, "started_at": datetime.now(UTC), "kickoff_id": None}
        result = self._db.builds.update_one(
            {"fde_id": self.fde_id, "id": build_id, "turn": None},
            {"$set": {"turn": turn, "updated_at": datetime.now(UTC)}},
        )
        return result.modified_count == 1

    def set_turn_kickoff(self, build_id: str, message_id: str, kickoff_id: str | None) -> None:
        self._db.builds.update_one(
            {"fde_id": self.fde_id, "id": build_id, "turn.id": message_id}, {"$set": {"turn.kickoff_id": kickoff_id}}
        )

    def end_turn(self, build_id: str, message_id: str) -> None:
        self._db.builds.update_one(
            {"fde_id": self.fde_id, "id": build_id, "turn.id": message_id},
            {"$set": {"turn": None, "updated_at": datetime.now(UTC)}},
        )

    def set_delivery(self, build_id: str, message_id: str, delivery: str) -> None:
        self._db.build_messages.update_one(
            {"fde_id": self.fde_id, "build_id": build_id, "id": message_id}, {"$set": {"delivery": delivery}}
        )

    def add_builder_note(self, build_id: str, turn_id: str, text: str) -> None:
        """A reply written by the app (a turn that failed on AMP), in the build's timeline."""
        counted = self._db.builds.find_one_and_update(
            {"fde_id": self.fde_id, "id": build_id},
            {"$inc": {"seq": 2}},
            projection={"seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if counted is None:
            return
        now = datetime.now(UTC)
        self._db.build_activity.insert_one(
            {
                "fde_id": self.fde_id,
                "build_id": build_id,
                "seq": counted["seq"] - 1,
                "turn_id": turn_id,
                "kind": "error",
                "text": text,
                "detail": {},
                "at": now,
            }
        )
        self._db.build_messages.insert_one(
            {
                "id": new_id(),
                "fde_id": self.fde_id,
                "build_id": build_id,
                "seq": counted["seq"],
                "role": "assistant",
                "author": "Builder",
                "text": f"{text}. Send your message again to retry.",
                "card": None,
                "turn_id": turn_id,
                "delivery": None,
                "envelope": None,
                "created_at": now,
            }
        )

    def overrides(self, day: str) -> dict[str, Any]:
        found = self._db.day_overrides.find_one({"fde_id": self.fde_id, "date": day}, {"_id": 0})
        return found or {"fde_id": self.fde_id, "date": day, "manual_entries": [], "comments": {}, "feedback": []}

    def save_overrides(self, overrides: dict[str, Any]) -> None:
        self._db.day_overrides.replace_one({"fde_id": self.fde_id, "date": overrides["date"]}, overrides, upsert=True)

    # Events -------------------------------------------------------------------------------------------

    def move_events(self, day: str, event_ids: list[str], account_key: str) -> None:
        """A moved topic takes its evidence events along; the rules' original account is kept in meta."""
        query = {"fde_id": self.fde_id, "date": day, "id": {"$in": event_ids}, "source": {"$ne": "manual"}}
        self._db.events.update_many(
            {**query, "meta.rule_account": {"$exists": False}},
            [{"$set": {"meta.rule_account": "$account_key"}}],
        )
        self._db.events.update_many(query, {"$set": {"account_key": account_key, "match_reason": "review"}})

    def delete_day(self, day: str) -> dict[str, int]:
        """Every record of the day, so it can be run again from scratch: what was collected, the log, your comments
        and additions, its review pauses and its runs. Todoist's sync records (action_items) are
        kept, with the tasks they created: a re-run skips only follow-ups worded exactly as before."""
        mine = {"fde_id": self.fde_id, "date": day}
        deleted = {
            name: self._db[name].delete_many(mine).deleted_count
            for name in (
                "daily_logs",
                "day_overrides",
                "review_requests",
                "events",
                "collected_events",
                "source_snapshots",
            )
        }
        deleted["runs"] = self._db.runs.delete_many({"fde_id": self.fde_id, "day": day}).deleted_count
        return deleted

    # Review requests ----------------------------------------------------------------------------------

    def waiting_reviews(self) -> list[dict[str, Any]]:
        found = self._db.review_requests.find({"fde_id": self.fde_id, "status": "waiting"}, {"_id": 0})
        return list(found.sort("created_at", DESCENDING))

    def latest_review(self, day: str) -> dict[str, Any] | None:
        return self._db.review_requests.find_one(
            {"fde_id": self.fde_id, "date": day}, {"_id": 0}, sort=[("round", DESCENDING)]
        )

    def review(self, review_id: str) -> dict[str, Any] | None:
        return self._db.review_requests.find_one({"fde_id": self.fde_id, "id": review_id}, {"_id": 0})

    def waiting_for_run(self, run_id: str) -> dict[str, Any] | None:
        return self._db.review_requests.find_one(
            {"run_id": run_id, "status": "waiting"}, {"_id": 0}, sort=[("round", DESCENDING)]
        )

    def attach_callback(self, review_id: str, amp_request_id: str, ciphertext: str) -> None:
        self._db.review_requests.update_one(
            {"id": review_id}, {"$set": {"amp_request_id": amp_request_id, "callback_ciphertext": ciphertext}}
        )

    def mark_answered(self, review_id: str) -> None:
        self._db.review_requests.update_one(
            {"id": review_id, "status": "waiting"},
            {"$set": {"status": "answered", "answered_at": datetime.now(UTC), "answered_from": "ui"}},
        )
