from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading

import pytest
from cryptography.fernet import Fernet

from aura_music_studio.esp_social_oauth import SocialOAuthVault


def _vault(tmp_path: Path, monkeypatch) -> SocialOAuthVault:
    path = tmp_path / "social-oauth.sqlite3"
    monkeypatch.setenv("AURA_SOCIAL_OAUTH_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    return SocialOAuthVault(path)


def test_new_oauth_state_is_opaque_and_digest_only_at_rest(tmp_path: Path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    state, _connection_id, _credential_id = vault.create_state(
        "tenant-a", "space-a", "tiktok"
    )

    with sqlite3.connect(vault.path) as con:
        stored_state = con.execute("SELECT state FROM esp_social_oauth_state").fetchone()[0]

    assert stored_state.startswith("sha256:")
    assert stored_state != state
    assert state.encode("utf-8") not in vault.path.read_bytes()
    assert vault.diagnostics()["oauth_state_storage"] == "sha256_digest"
    assert vault.diagnostics()["oauth_state_single_use"] is True


def test_wrong_member_or_provider_cannot_consume_valid_state(tmp_path: Path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    state, _connection_id, _credential_id = vault.create_state(
        "tenant-a", "space-a", "tiktok"
    )

    with pytest.raises(PermissionError, match="invalid or expired"):
        vault.consume_state("tenant-b", "tiktok", state)
    with pytest.raises(PermissionError, match="invalid or expired"):
        vault.consume_state("tenant-a", "youtube", state)

    pending = vault.consume_state("tenant-a", "tiktok", state)
    assert pending["space_id"] == "space-a"
    with pytest.raises(PermissionError, match="invalid or expired"):
        vault.consume_state("tenant-a", "tiktok", state)


def test_expired_state_is_rejected_and_removed(tmp_path: Path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    state, _connection_id, _credential_id = vault.create_state(
        "tenant-a", "space-a", "instagram"
    )
    with sqlite3.connect(vault.path) as con:
        con.execute(
            "UPDATE esp_social_oauth_state SET created_at=?",
            (datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),),
        )

    with pytest.raises(PermissionError, match="invalid or expired"):
        vault.consume_state("tenant-a", "instagram", state)

    with sqlite3.connect(vault.path) as con:
        remaining = con.execute("SELECT COUNT(*) FROM esp_social_oauth_state").fetchone()[0]
    assert remaining == 0


def test_concurrent_callbacks_can_consume_state_exactly_once(tmp_path: Path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    state, _connection_id, _credential_id = vault.create_state(
        "tenant-a", "space-a", "youtube"
    )
    workers = 6
    barrier = threading.Barrier(workers)

    def consume_once() -> bool:
        barrier.wait()
        try:
            vault.consume_state("tenant-a", "youtube", state)
        except PermissionError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _index: consume_once(), range(workers)))

    assert results.count(True) == 1
    assert results.count(False) == workers - 1


def test_legacy_plaintext_state_can_finish_once_during_migration(tmp_path: Path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    state, _connection_id, _credential_id = vault.create_state(
        "tenant-a", "space-a", "tiktok"
    )
    with sqlite3.connect(vault.path) as con:
        con.execute("UPDATE esp_social_oauth_state SET state=?", (state,))

    pending = vault.consume_state("tenant-a", "tiktok", state)
    assert pending["space_id"] == "space-a"
    with pytest.raises(PermissionError, match="invalid or expired"):
        vault.consume_state("tenant-a", "tiktok", state)
