from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request

from .audit import AuditLedger
from .protected_data_authority import (
    approved_protected_record_target,
    authorize_protected_data_access,
)

_FORMAT = "aura-sec-recovery-vault"
_VERSION = 1
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class RecoveryVaultError(RuntimeError):
    """Fail-closed error for protected-data backup/recovery operations."""


class RecoveryStorage(Protocol):
    """Minimum storage authority needed by Aura Sec.

    Implementations intentionally expose no list, update, move, share or delete operation.
    That keeps the storage identity incapable of broad Drive administration through this API.
    """

    def create(self, *, folder_id: str, name: str, payload: bytes) -> str: ...

    def read(self, *, folder_id: str, object_id: str) -> bytes: ...


@dataclass(frozen=True)
class RecoveryReceipt:
    backup_id: str
    object_id: str
    ciphertext_sha256: str
    key_id: str
    verified: bool


@dataclass(frozen=True)
class _Keyring:
    active_key_id: str
    keys: dict[str, bytes]

    @classmethod
    def from_environment(cls) -> "_Keyring":
        active = (os.getenv("AURA_SEC_RECOVERY_ACTIVE_KEY_ID") or "").strip()
        raw = (os.getenv("AURA_SEC_RECOVERY_KEYS_JSON") or "").strip()
        if not active or not _KEY_ID_RE.fullmatch(active) or not raw:
            raise RecoveryVaultError("recovery vault key configuration unavailable")
        try:
            configured = json.loads(raw)
        except Exception as exc:
            raise RecoveryVaultError("recovery vault key configuration invalid") from exc
        if not isinstance(configured, dict) or active not in configured:
            raise RecoveryVaultError("active recovery key unavailable")

        keys: dict[str, bytes] = {}
        for key_id, encoded in configured.items():
            if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
                raise RecoveryVaultError("recovery key id invalid")
            if not isinstance(encoded, str):
                raise RecoveryVaultError("recovery key encoding invalid")
            try:
                key = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise RecoveryVaultError("recovery key encoding invalid") from exc
            if len(key) != 32:
                raise RecoveryVaultError("recovery keys must be 256-bit")
            keys[key_id] = key
        return cls(active_key_id=active, keys=keys)

    def active(self) -> tuple[str, bytes]:
        return self.active_key_id, self.keys[self.active_key_id]

    def get(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise RecoveryVaultError("recovery key unavailable") from exc


class GoogleDriveRecoveryStorage:
    """Narrow Google Drive v3 adapter for encrypted Aura Sec blobs only.

    The bearer token is supplied by server-side deployment credential management. This
    adapter never lists Drive contents and never updates, moves, shares or deletes files.
    Every read first verifies that the requested object still belongs to the approved
    folder, preventing an arbitrary Drive file ID from becoming a read primitive.
    """

    api_root = "https://www.googleapis.com/drive/v3"
    upload_root = "https://www.googleapis.com/upload/drive/v3"

    def __init__(self, bearer_token: str | None = None, *, timeout_seconds: float = 30.0):
        self.bearer_token = (
            bearer_token
            if bearer_token is not None
            else os.getenv("AURA_SEC_PROTECTED_DATA_DRIVE_BEARER_TOKEN")
        ) or ""
        self.bearer_token = self.bearer_token.strip()
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        if not self.bearer_token:
            raise RecoveryVaultError("protected Drive service credential unavailable")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    @staticmethod
    def _checked_id(value: str, *, label: str) -> str:
        value = (value or "").strip()
        if not _DRIVE_ID_RE.fullmatch(value):
            raise RecoveryVaultError(f"{label} invalid")
        return value

    def _metadata(self, object_id: str) -> dict:
        object_id = self._checked_id(object_id, label="Drive object id")
        response = requests.get(
            f"{self.api_root}/files/{quote(object_id, safe='')}",
            headers=self._headers,
            params={"fields": "id,parents,trashed", "supportsAllDrives": "true"},
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise RecoveryVaultError("protected Drive metadata verification failed")
        try:
            data = response.json()
        except Exception as exc:
            raise RecoveryVaultError("protected Drive metadata response invalid") from exc
        if data.get("id") != object_id or data.get("trashed") is True:
            raise RecoveryVaultError("protected Drive object unavailable")
        return data

    def create(self, *, folder_id: str, name: str, payload: bytes) -> str:
        folder_id = self._checked_id(folder_id, label="Drive folder id")
        if not payload:
            raise RecoveryVaultError("encrypted recovery payload empty")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name or "")[:120]
        if not safe_name:
            raise RecoveryVaultError("recovery object name invalid")

        boundary = "aura-sec-" + secrets.token_hex(16)
        metadata = json.dumps(
            {"name": safe_name, "parents": [folder_id]}, separators=(",", ":")
        ).encode("utf-8")
        body = b"".join(
            [
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                metadata,
                f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
                payload,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        headers = {
            **self._headers,
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
        response = requests.post(
            f"{self.upload_root}/files",
            headers=headers,
            params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": "true"},
            data=body,
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 201}:
            raise RecoveryVaultError("protected Drive write failed")
        try:
            object_id = self._checked_id(response.json().get("id", ""), label="Drive object id")
        except Exception as exc:
            if isinstance(exc, RecoveryVaultError):
                raise
            raise RecoveryVaultError("protected Drive write response invalid") from exc
        metadata_after = self._metadata(object_id)
        if folder_id not in (metadata_after.get("parents") or []):
            raise RecoveryVaultError("protected Drive destination verification failed")
        return object_id

    def read(self, *, folder_id: str, object_id: str) -> bytes:
        folder_id = self._checked_id(folder_id, label="Drive folder id")
        object_id = self._checked_id(object_id, label="Drive object id")
        metadata = self._metadata(object_id)
        if folder_id not in (metadata.get("parents") or []):
            raise RecoveryVaultError("protected Drive source outside approved vault")
        response = requests.get(
            f"{self.api_root}/files/{quote(object_id, safe='')}",
            headers=self._headers,
            params={"alt": "media", "supportsAllDrives": "true"},
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise RecoveryVaultError("protected Drive read failed")
        return bytes(response.content)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _aad(*, backup_id: str, subject_user_id: str, record_type: str, folder_id: str) -> bytes:
    if not subject_user_id or len(subject_user_id) > 256:
        raise RecoveryVaultError("protected subject invalid")
    if not record_type or len(record_type) > 120:
        raise RecoveryVaultError("protected record type invalid")
    binding = {
        "format": _FORMAT,
        "version": _VERSION,
        "backup_id": backup_id,
        "subject_user_id": subject_user_id,
        "record_type": record_type,
        "folder_id": folder_id,
    }
    return json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_envelope(*, backup_id: str, key_id: str, nonce: bytes, ciphertext: bytes) -> bytes:
    envelope = {
        "format": _FORMAT,
        "version": _VERSION,
        "backup_id": backup_id,
        "created_at": _now(),
        "key_id": key_id,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "ciphertext_sha256": _sha256(ciphertext),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_envelope(payload: bytes) -> tuple[dict, bytes, bytes]:
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RecoveryVaultError("recovery envelope invalid") from exc
    if not isinstance(envelope, dict):
        raise RecoveryVaultError("recovery envelope invalid")
    if envelope.get("format") != _FORMAT or envelope.get("version") != _VERSION:
        raise RecoveryVaultError("recovery envelope version unsupported")
    backup_id = envelope.get("backup_id")
    key_id = envelope.get("key_id")
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{32}", backup_id):
        raise RecoveryVaultError("recovery backup id invalid")
    if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
        raise RecoveryVaultError("recovery key id invalid")
    try:
        nonce = base64.b64decode(envelope["nonce_b64"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext_b64"], validate=True)
    except Exception as exc:
        raise RecoveryVaultError("recovery ciphertext encoding invalid") from exc
    if len(nonce) != 12 or not ciphertext:
        raise RecoveryVaultError("recovery ciphertext invalid")
    expected = envelope.get("ciphertext_sha256")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, _sha256(ciphertext)):
        raise RecoveryVaultError("recovery ciphertext integrity check failed")
    return envelope, nonce, ciphertext


class AuraSecRecoveryVault:
    """Encrypted, versioned, fail-closed protected-record backup and recovery primitive."""

    def __init__(
        self,
        storage: RecoveryStorage,
        *,
        audit: AuditLedger | None = None,
        keyring: _Keyring | None = None,
    ):
        self.storage = storage
        self.audit = audit or AuditLedger()
        self.keyring = keyring or _Keyring.from_environment()

    @staticmethod
    def _admit_target(folder_id: str) -> str:
        folder_id = (folder_id or "").strip()
        if not approved_protected_record_target(provider="google_drive", target_id=folder_id):
            raise RecoveryVaultError("protected recovery destination not admitted")
        return folder_id

    def backup(
        self,
        *,
        subject_user_id: str,
        record_type: str,
        plaintext: bytes,
        folder_id: str,
    ) -> RecoveryReceipt:
        """Encrypt, create, re-read and verify a new immutable recovery object."""

        folder_id = self._admit_target(folder_id)
        if not isinstance(plaintext, bytes) or not plaintext:
            raise RecoveryVaultError("protected recovery plaintext empty")
        backup_id = uuid4().hex
        key_id, key = self.keyring.active()
        aad = _aad(
            backup_id=backup_id,
            subject_user_id=subject_user_id,
            record_type=record_type,
            folder_id=folder_id,
        )
        try:
            self.audit.append(
                actor="Aura Sec Recovery Vault",
                action="recovery_vault_backup_authorized",
                subject_user_id=subject_user_id,
                details={"backup_id": backup_id, "record_type": record_type[:120], "key_id": key_id},
            )
        except Exception as exc:
            raise RecoveryVaultError("recovery audit unavailable") from exc

        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
        envelope = _encode_envelope(
            backup_id=backup_id, key_id=key_id, nonce=nonce, ciphertext=ciphertext
        )
        object_id = self.storage.create(
            folder_id=folder_id,
            name=f"aura-sec-{backup_id}.vault",
            payload=envelope,
        )

        reread = self.storage.read(folder_id=folder_id, object_id=object_id)
        if not secrets.compare_digest(_sha256(reread), _sha256(envelope)):
            raise RecoveryVaultError("recovery post-write verification failed")
        verified_envelope, _, verified_ciphertext = _decode_envelope(reread)
        if verified_envelope["backup_id"] != backup_id:
            raise RecoveryVaultError("recovery post-write identity mismatch")

        try:
            self.audit.append(
                actor="Aura Sec Recovery Vault",
                action="recovery_vault_backup_verified",
                subject_user_id=subject_user_id,
                details={
                    "backup_id": backup_id,
                    "record_type": record_type[:120],
                    "key_id": key_id,
                    "object_ref_sha256": _sha256(object_id.encode("utf-8")),
                    "ciphertext_sha256": _sha256(verified_ciphertext),
                },
            )
        except Exception as exc:
            raise RecoveryVaultError("recovery audit unavailable after verification") from exc

        return RecoveryReceipt(
            backup_id=backup_id,
            object_id=object_id,
            ciphertext_sha256=_sha256(verified_ciphertext),
            key_id=key_id,
            verified=True,
        )

    def recover(
        self,
        request: Request,
        *,
        subject_user_id: str,
        record_type: str,
        object_id: str,
        folder_id: str,
    ) -> bytes:
        """Return plaintext only after protected authority, integrity and AEAD validation."""

        folder_id = self._admit_target(folder_id)
        decision = authorize_protected_data_access(
            request,
            action="recovery_vault_restore",
            subject_user_id=subject_user_id,
            audit=self.audit,
        )
        if not decision.allowed:
            raise RecoveryVaultError("protected recovery access denied")

        payload = self.storage.read(folder_id=folder_id, object_id=object_id)
        envelope, nonce, ciphertext = _decode_envelope(payload)
        backup_id = envelope["backup_id"]
        key_id = envelope["key_id"]
        aad = _aad(
            backup_id=backup_id,
            subject_user_id=subject_user_id,
            record_type=record_type,
            folder_id=folder_id,
        )
        try:
            plaintext = AESGCM(self.keyring.get(key_id)).decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise RecoveryVaultError("recovery authentication failed") from exc

        try:
            self.audit.append(
                actor=f"{decision.persona.title()} · Aura Sec Recovery Vault",
                action="recovery_vault_restore_verified",
                subject_user_id=subject_user_id,
                details={
                    "backup_id": backup_id,
                    "record_type": record_type[:120],
                    "key_id": key_id,
                    "object_ref_sha256": _sha256(object_id.encode("utf-8")),
                },
            )
        except Exception as exc:
            raise RecoveryVaultError("recovery audit unavailable after verification") from exc
        return plaintext
