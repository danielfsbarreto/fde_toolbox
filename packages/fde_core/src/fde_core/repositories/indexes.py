"""Collections and indexes. Idempotent: safe to run on every startup."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.database import Database

EVENTS = "events"
DAILY_LOGS = "daily_logs"
RUNS = "runs"
REFERENCE_CACHE = "reference_cache"
DAY_OVERRIDES = "day_overrides"
COLLECTED_EVENTS = "collected_events"
SOURCE_SNAPSHOTS = "source_snapshots"
ACTION_ITEMS = "action_items"
REVIEW_REQUESTS = "review_requests"
LEARNINGS = "learnings"
REPORTS = "reports"
BUILDS = "builds"
BUILD_ARTIFACTS = "build_artifacts"
BUILD_MESSAGES = "build_messages"
BUILD_ACTIVITY = "build_activity"
BUILD_ENV = "build_env"

INDEXES: dict[str, list[IndexModel]] = {
    EVENTS: [
        IndexModel([("fde_id", ASCENDING), ("id", ASCENDING)], unique=True, name="fde_event_unique"),
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING), ("source", ASCENDING)], name="fde_date_source"),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_at_ttl"),
    ],
    DAILY_LOGS: [
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING)], unique=True, name="fde_date_unique"),
    ],
    RUNS: [
        IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
        IndexModel([("fde_id", ASCENDING), ("started_at", DESCENDING)], name="fde_started_at"),
    ],
    COLLECTED_EVENTS: [
        IndexModel([("fde_id", ASCENDING), ("id", ASCENDING)], unique=True, name="fde_event_unique"),
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING), ("source", ASCENDING)], name="fde_date_source"),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_at_ttl"),
    ],
    SOURCE_SNAPSHOTS: [
        IndexModel(
            [("fde_id", ASCENDING), ("date", ASCENDING), ("source", ASCENDING)], unique=True, name="fde_day_source"
        ),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_at_ttl"),
    ],
    DAY_OVERRIDES: [
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING)], unique=True, name="fde_date_unique"),
    ],
    REFERENCE_CACHE: [
        IndexModel([("fde_id", ASCENDING), ("kind", ASCENDING)], unique=True, name="fde_kind_unique"),
    ],
    REVIEW_REQUESTS: [
        IndexModel([("id", ASCENDING)], unique=True, name="id_unique"),
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING), ("round", DESCENDING)], name="fde_date_round"),
        IndexModel([("run_id", ASCENDING), ("status", ASCENDING)], name="run_status"),
    ],
    ACTION_ITEMS: [
        IndexModel([("fde_id", ASCENDING), ("key", ASCENDING)], unique=True, name="fde_key_unique"),
        IndexModel([("fde_id", ASCENDING), ("date", ASCENDING)], name="fde_date"),
    ],
    LEARNINGS: [
        IndexModel([("fde_id", ASCENDING), ("id", ASCENDING)], unique=True, name="fde_id_unique"),
        IndexModel([("fde_id", ASCENDING), ("status", ASCENDING)], name="fde_status"),
    ],
    REPORTS: [
        IndexModel([("id", ASCENDING)], unique=True, name="id_unique"),
        IndexModel([("fde_id", ASCENDING), ("created_at", DESCENDING)], name="fde_created_at"),
    ],
    BUILDS: [
        IndexModel([("id", ASCENDING)], unique=True, name="id_unique"),
        IndexModel([("session_id", ASCENDING)], unique=True, name="session_unique"),
        IndexModel([("fde_id", ASCENDING), ("updated_at", DESCENDING)], name="fde_updated_at"),
    ],
    BUILD_ARTIFACTS: [
        IndexModel(
            [("build_id", ASCENDING), ("stage", ASCENDING), ("version", ASCENDING)], unique=True, name="stage_version"
        ),
    ],
    BUILD_MESSAGES: [
        IndexModel([("build_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="build_seq"),
        IndexModel([("fde_id", ASCENDING), ("delivery", ASCENDING)], name="fde_delivery"),
    ],
    BUILD_ACTIVITY: [
        IndexModel([("build_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="build_seq"),
    ],
    BUILD_ENV: [
        IndexModel(
            [("fde_id", ASCENDING), ("build_id", ASCENDING), ("name", ASCENDING)], unique=True, name="build_name"
        ),
        IndexModel([("fde_id", ASCENDING), ("name", ASCENDING)], name="fde_name"),  # reuse across builds
    ],
}


def ensure_indexes(db: Database) -> dict[str, list[str]]:
    existing = set(db.list_collection_names())
    created: dict[str, list[str]] = {}
    for name, indexes in INDEXES.items():
        if name not in existing:
            db.create_collection(name)
        created[name] = db[name].create_indexes(indexes)
    return created
