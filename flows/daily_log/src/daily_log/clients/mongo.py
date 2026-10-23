from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.server_api import ServerApi


def connect(connection_string: str, database: str, server_selection_timeout_ms: int = 10_000) -> Database:
    client: MongoClient = MongoClient(
        connection_string,
        server_api=ServerApi("1"),
        tz_aware=True,
        appname="fde-activity-log",
        serverSelectionTimeoutMS=server_selection_timeout_ms,
    )
    return client[database]
