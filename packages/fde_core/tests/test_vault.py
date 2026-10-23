"""A build's env var values: encrypted at rest, decrypted only for its sandbox, scrubbed from output."""

import mongomock
import pytest
from cryptography.fernet import Fernet

from fde_core.domain.builds import Build
from fde_core.repositories.build_env import BuildEnvRepository
from fde_core.vault import EnvVault, VaultError, scrub


def test_values_round_trip_and_a_wrong_key_fails():
    vault = EnvVault(Fernet.generate_key().decode())
    sealed = vault.seal("sk-ant-secret")
    assert "sk-ant-secret" not in sealed and vault.open(sealed) == "sk-ant-secret"
    with pytest.raises(VaultError):
        EnvVault(Fernet.generate_key().decode()).open(sealed)


def test_scrub_replaces_values_by_their_names():
    values = {"ANTHROPIC_API_KEY": "sk-ant-123456", "LIMIT": "3", "KEY_PREFIX": "sk-ant"}
    text = "using sk-ant-123456, limit 3"
    assert scrub(text, values) == "using [redacted:ANTHROPIC_API_KEY], limit 3"  # longest first; short ones left alone


def test_the_repository_reads_one_builds_values_only():
    db = mongomock.MongoClient().db
    vault = EnvVault(Fernet.generate_key().decode())
    db.build_env.insert_many(
        [
            {"fde_id": "f", "build_id": "b1", "name": "EXA_API_KEY", "ciphertext": vault.seal("exa-1")},
            {"fde_id": "f", "build_id": "b2", "name": "EXA_API_KEY", "ciphertext": vault.seal("exa-2")},
            {"fde_id": "other", "build_id": "b1", "name": "SERPER_API_KEY", "ciphertext": vault.seal("x")},
        ]
    )
    build = Build.model_validate(
        {
            "id": "b1",
            "fde_id": "f",
            "title": "t",
            "session_id": "s",
            "created_at": "2026-10-21T00:00:00Z",
            "updated_at": "2026-10-21T00:00:00Z",
        }
    )
    repo = BuildEnvRepository(db)
    assert repo.names(build) == {"EXA_API_KEY"}
    assert repo.values(build, vault) == {"EXA_API_KEY": "exa-1"}
