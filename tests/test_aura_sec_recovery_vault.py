from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from aura_music_studio import aura_sec_recovery_vault as rv


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.folders = {}
        self.counter = 0

    def create(self, *, folder_id: str, name: str, payload: bytes) -> str:
        self.counter += 1
        object_id = f"vault_object_{self.counter:08d}"
        self.objects[object_id] = bytes(payload)
        self.folders[object_id] = folder_id
        return object_id

    def read(self, *, folder_id: str, object_id: str) -> bytes:
        if self.folders.get(object_id) != folder_id:
            raise rv.RecoveryVaultError("wrong folder")
        return self.objects[object_id]


class Audit:
    def __init__(self, fail_on_action: str | None = None):
        self.entries = []
        self.fail_on_action = fail_on_action

    def append(self, **entry):
        if entry["action"] == self.fail_on_action:
            raise RuntimeError("audit unavailable")
        self.entries.append(entry)
        return entry


def keyring():
    return rv._Keyring(active_key_id="2026-09", keys={"2026-09": b"K" * 32})


def admit(monkeypatch, folder="approved_folder_123"):
    monkeypatch.setenv("AURA_SEC_PROTECTED_DATA_DRIVE_FOLDER_ID", folder)
    return folder


def allow_recovery(monkeypatch, persona="kev"):
    monkeypatch.setattr(
        rv,
        "authorize_protected_data_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, persona=persona),
    )


def test_backup_encrypts_plaintext_and_verifies_write(monkeypatch):
    folder = admit(monkeypatch)
    storage = MemoryStorage()
    audit = Audit()
    vault = rv.AuraSecRecoveryVault(storage, audit=audit, keyring=keyring())
    secret = b"private-user-record:do-not-expose"

    receipt = vault.backup(
        subject_user_id="opaque-user-1",
        record_type="account-record",
        plaintext=secret,
        folder_id=folder,
    )

    assert receipt.verified is True
    envelope_bytes = storage.objects[receipt.object_id]
    assert secret not in envelope_bytes
    envelope = json.loads(envelope_bytes)
    assert envelope["format"] == "aura-sec-recovery-vault"
    assert envelope["version"] == 1
    assert envelope["key_id"] == "2026-09"
    assert "opaque-user-1" not in envelope_bytes.decode()
    assert audit.entries[-1]["action"] == "recovery_vault_backup_verified"
    assert "object_id" not in audit.entries[-1]["details"]


def test_backup_rejects_unapproved_destination_before_storage_write(monkeypatch):
    admit(monkeypatch)
    storage = MemoryStorage()
    vault = rv.AuraSecRecoveryVault(storage, audit=Audit(), keyring=keyring())

    with pytest.raises(rv.RecoveryVaultError, match="destination not admitted"):
        vault.backup(
            subject_user_id="opaque-user-1",
            record_type="account-record",
            plaintext=b"private",
            folder_id="other_folder_123",
        )
    assert storage.objects == {}


def test_recover_requires_protected_data_authority(monkeypatch):
    folder = admit(monkeypatch)
    storage = MemoryStorage()
    vault = rv.AuraSecRecoveryVault(storage, audit=Audit(), keyring=keyring())
    receipt = vault.backup(
        subject_user_id="opaque-user-1",
        record_type="account-record",
        plaintext=b"private",
        folder_id=folder,
    )
    monkeypatch.setattr(
        rv,
        "authorize_protected_data_access",
        lambda *args, **kwargs: SimpleNamespace(allowed=False, persona=None),
    )

    with pytest.raises(rv.RecoveryVaultError, match="access denied"):
        vault.recover(
            SimpleNamespace(cookies={}),
            subject_user_id="opaque-user-1",
            record_type="account-record",
            object_id=receipt.object_id,
            folder_id=folder,
        )


def test_recover_roundtrip_is_subject_and_record_type_bound(monkeypatch):
    folder = admit(monkeypatch)
    allow_recovery(monkeypatch)
    storage = MemoryStorage()
    audit = Audit()
    vault = rv.AuraSecRecoveryVault(storage, audit=audit, keyring=keyring())
    secret = b"private user record"
    receipt = vault.backup(
        subject_user_id="opaque-user-1",
        record_type="account-record",
        plaintext=secret,
        folder_id=folder,
    )

    restored = vault.recover(
        SimpleNamespace(cookies={}),
        subject_user_id="opaque-user-1",
        record_type="account-record",
        object_id=receipt.object_id,
        folder_id=folder,
    )
    assert restored == secret
    assert audit.entries[-1]["action"] == "recovery_vault_restore_verified"

    with pytest.raises(rv.RecoveryVaultError, match="authentication failed"):
        vault.recover(
            SimpleNamespace(cookies={}),
            subject_user_id="opaque-user-2",
            record_type="account-record",
            object_id=receipt.object_id,
            folder_id=folder,
        )

    with pytest.raises(rv.RecoveryVaultError, match="authentication failed"):
        vault.recover(
            SimpleNamespace(cookies={}),
            subject_user_id="opaque-user-1",
            record_type="different-record",
            object_id=receipt.object_id,
            folder_id=folder,
        )


def test_tampered_ciphertext_is_rejected(monkeypatch):
    folder = admit(monkeypatch)
    allow_recovery(monkeypatch, "mary")
    storage = MemoryStorage()
    vault = rv.AuraSecRecoveryVault(storage, audit=Audit(), keyring=keyring())
    receipt = vault.backup(
        subject_user_id="opaque-user-1",
        record_type="account-record",
        plaintext=b"private",
        folder_id=folder,
    )
    envelope = json.loads(storage.objects[receipt.object_id])
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext_b64"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext_b64"] = base64.b64encode(bytes(ciphertext)).decode()
    storage.objects[receipt.object_id] = json.dumps(envelope).encode()

    with pytest.raises(rv.RecoveryVaultError, match="integrity check failed"):
        vault.recover(
            SimpleNamespace(cookies={}),
            subject_user_id="opaque-user-1",
            record_type="account-record",
            object_id=receipt.object_id,
            folder_id=folder,
        )


def test_keyring_requires_256_bit_active_key(monkeypatch):
    monkeypatch.setenv("AURA_SEC_RECOVERY_ACTIVE_KEY_ID", "current")
    monkeypatch.setenv(
        "AURA_SEC_RECOVERY_KEYS_JSON",
        json.dumps({"current": base64.b64encode(b"short").decode()}),
    )
    with pytest.raises(rv.RecoveryVaultError, match="256-bit"):
        rv._Keyring.from_environment()


def test_backup_fails_closed_when_audit_is_unavailable(monkeypatch):
    folder = admit(monkeypatch)
    storage = MemoryStorage()
    vault = rv.AuraSecRecoveryVault(
        storage,
        audit=Audit(fail_on_action="recovery_vault_backup_authorized"),
        keyring=keyring(),
    )
    with pytest.raises(rv.RecoveryVaultError, match="audit unavailable"):
        vault.backup(
            subject_user_id="opaque-user-1",
            record_type="account-record",
            plaintext=b"private",
            folder_id=folder,
        )
    assert storage.objects == {}
