"""Composition root: settings, the database and the turn service. Built once per process; nothing here belongs to a
single turn or build, so turns of different builds can run side by side."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache

from dotenv import load_dotenv
from pymongo.database import Database

from fde_core.repositories.build_env import BuildEnvRepository
from fde_core.repositories.builds import MongoBuildRepository
from fde_core.repositories.indexes import ensure_indexes
from project_builder.services.builder import Builder
from project_builder.settings import Settings


@dataclass(frozen=True)
class App:
    settings: Settings
    db: Database
    builds: MongoBuildRepository
    builder: Builder


@cache
def app() -> App:
    load_dotenv(override=False)
    logging.getLogger("project_builder").setLevel(logging.INFO)
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment (pydantic-settings)
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi

    db = MongoClient(
        settings.mongodb_connection_string.get_secret_value(),
        server_api=ServerApi("1"),
        tz_aware=True,
        appname="fde-activity-log-builder",
    )[settings.mongodb_database]
    ensure_indexes(db)
    builds = MongoBuildRepository(db)
    return App(settings=settings, db=db, builds=builds, builder=Builder(builds, settings, BuildEnvRepository(db)))
