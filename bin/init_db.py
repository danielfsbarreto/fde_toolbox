"""Create the database, collections and indexes in MongoDB Atlas. Idempotent.

bin/flow daily_log python bin/init_db.py   (reads flows/daily_log/.env)
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from daily_log.clients.mongo import connect
from daily_log.settings import Settings
from fde_core.repositories.indexes import ensure_indexes


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / "flows" / "daily_log" / ".env", override=False)
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment (pydantic-settings)
    db = connect(settings.mongodb_connection_string.get_secret_value(), settings.mongodb_database)
    created = ensure_indexes(db)
    print(f"database: {db.name}")
    for collection, indexes in created.items():
        print(f"  {collection}: {', '.join(indexes)}")


if __name__ == "__main__":
    main()
