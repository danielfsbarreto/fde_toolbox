from __future__ import annotations

import functools
from dataclasses import dataclass

from dotenv import load_dotenv

from review_api.callbacks import CallbackVault
from review_api.edits import Edits
from review_api.env_vault import EnvVault
from review_api.settings import Settings
from review_api.store import Store, connect


@dataclass(frozen=True)
class Server:
    settings: Settings
    store: Store
    edits: Edits
    vault: CallbackVault
    env_vault: EnvVault | None  # None until BUILD_ENV_KEY is set: env values cannot be saved


@functools.cache
def server() -> Server:
    load_dotenv(override=False)
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment (pydantic-settings)
    store = Store(
        connect(settings.mongodb_connection_string.get_secret_value(), settings.mongodb_database), settings.fde_id
    )
    return Server(
        settings=settings,
        store=store,
        edits=Edits(store),
        vault=CallbackVault(settings.callback_encryption_key.get_secret_value()),
        env_vault=EnvVault(settings.build_env_key.get_secret_value()) if settings.build_env_key else None,
    )
