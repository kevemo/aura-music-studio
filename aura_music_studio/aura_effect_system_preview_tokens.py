from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PREVIEW_TOKEN_TTL_SECONDS = 15 * 60
MAX_ACTIVE_PREVIEW_TOKENS = 512
ISSUANCE_LOCK_WAIT_SECONDS = 2.0
ISSUANCE_LOCK_STALE_SECONDS = 10.0
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _preview_root(project: Path) -> Path:
    project_root = project.resolve()
    root = (project_root / "work" / "effect_system_previews").resolve()
    if project_root not in root.parents:
        raise ValueError("Effect-system preview evidence path escapes project storage")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_token(token: str) -> str:
    normalized = str(token or "").strip().casefold()
    if not _TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError("Effect-system preview token is invalid")
    return normalized


def _token_storage_key(token: str) -> str:
    """Derive a non-reversible lookup key so raw bearer proofs are never persisted as filenames."""
    normalized = _normalize_token(token)
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def _token_path(project: Path, token: str) -> Path:
    storage_key = _token_storage_key(token)
    root = _preview_root(project).resolve()
    target = (root / f"{storage_key}.json").resolve()
    if root not in target.parents:
        raise ValueError("Effect-system preview token path escapes project storage")
    return target


def _claim_path(project: Path, token: str) -> Path:
    """Allocate a same-directory private claim path used for atomic one-time consumption."""
    storage_key = _token_storage_key(token)
    root = _preview_root(project).resolve()
    target = (root / f".{storage_key}.{secrets.token_hex(8)}.claim").resolve()
    if root not in target.parents:
        raise ValueError("Effect-system preview claim path escapes project storage")
    return target


def _issuance_lock_path(root: Path) -> Path:
    target = (root.resolve() / ".issue.lock").resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Effect-system preview issuance lock path escapes project storage")
    return target


def _lock_owner(lock_path: Path) -> str:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("owner") or "")


@contextmanager
def _issuance_lock(root: Path) -> Iterator[None]:
    """Serialize cleanup, quota admission and proof creation across concurrent issuers."""
    lock_path = _issuance_lock_path(root)
    owner = secrets.token_hex(16)
    deadline = time.monotonic() + ISSUANCE_LOCK_WAIT_SECONDS
    while True:
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                json.dump({"created_at": time.time(), "owner": owner}, handle, separators=(",", ":"))
            break
        except FileExistsError:
            try:
                age = max(0.0, time.time() - lock_path.stat().st_mtime)
                if age > ISSUANCE_LOCK_STALE_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Effect-system preview issuance is busy; retry shortly")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            if _lock_owner(lock_path) == owner:
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _clean_binding(value: str, *, label: str, max_length: int = 160) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if len(normalized) > max_length:
        raise ValueError(f"{label} is too long")
    if any(ord(ch) < 32 for ch in normalized):
        raise ValueError(f"{label} contains unsupported control characters")
    return normalized


def _clean_fingerprint(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError("Effect-system fingerprint must be a 64-character SHA-256 hex digest")
    return normalized


def _cleanup_expired(root: Path, now: float) -> int:
    removed = 0
    for pattern in ("*.json", "*.claim"):
        for path in root.glob(pattern):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                expires_at = float(payload.get("expires_at") or 0.0) if isinstance(payload, dict) else 0.0
            except Exception:
                expires_at = 0.0
            if expires_at <= now:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    continue
    return removed


def issue_effect_system_preview_token(
    project: Path,
    *,
    user_id: str,
    track_id: str,
    fingerprint: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Persist one short-lived opaque preview proof inside tenant-scoped project storage."""
    issued_at = float(time.time() if now is None else now)
    if issued_at < 0:
        raise ValueError("Preview issue time is invalid")
    normalized_user = _clean_binding(user_id, label="Preview user id", max_length=128)
    normalized_track = _clean_binding(track_id, label="Preview track id", max_length=160)
    normalized_fingerprint = _clean_fingerprint(fingerprint)
    root = _preview_root(project)

    with _issuance_lock(root):
        _cleanup_expired(root, issued_at)
        active = sum(1 for path in root.glob("*.json") if path.is_file())
        if active >= MAX_ACTIVE_PREVIEW_TOKENS:
            raise RuntimeError("Too many active effect-system preview tokens; retry after existing previews expire")

        expires_at = issued_at + PREVIEW_TOKEN_TTL_SECONDS
        payload = {
            "schema_version": 1,
            "user_id": normalized_user,
            "track_id": normalized_track,
            "fingerprint": normalized_fingerprint,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        for _attempt in range(4):
            token = secrets.token_hex(32)
            path = _token_path(project, token)
            try:
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                return {
                    "token": token,
                    "expires_at": expires_at,
                    "expires_in_seconds": PREVIEW_TOKEN_TTL_SECONDS,
                    "one_time": True,
                    "server_authoritative": True,
                    "raw_token_persisted": False,
                    "quota_admission_serialized": True,
                }
            except FileExistsError:
                continue
    raise RuntimeError("Unable to allocate unique effect-system preview token")


def consume_effect_system_preview_token(
    project: Path,
    token: str,
    *,
    user_id: str,
    track_id: str,
    fingerprint: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Atomically claim and consume one preview proof exactly once, failing closed on mismatch."""
    normalized_token = _normalize_token(token)
    path = _token_path(project, normalized_token)
    claimed_path = _claim_path(project, normalized_token)
    try:
        path.rename(claimed_path)
    except FileNotFoundError as exc:
        raise PermissionError("Effect-system preview token is missing, expired or already consumed") from exc
    except OSError as exc:
        raise PermissionError("Effect-system preview token could not be claimed for one-time use") from exc

    try:
        raw = claimed_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PermissionError("Effect-system preview token could not be read") from exc
    finally:
        try:
            claimed_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise PermissionError("Effect-system preview token evidence is invalid") from exc
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise PermissionError("Effect-system preview token evidence is invalid")

    expected_user = _clean_binding(user_id, label="Preview user id", max_length=128)
    expected_track = _clean_binding(track_id, label="Preview track id", max_length=160)
    expected_fingerprint = _clean_fingerprint(fingerprint)
    current = float(time.time() if now is None else now)
    try:
        expires_at = float(payload.get("expires_at") or 0.0)
    except (TypeError, ValueError) as exc:
        raise PermissionError("Effect-system preview token expiry evidence is invalid") from exc
    if expires_at <= current:
        raise PermissionError("Effect-system preview token has expired")
    if str(payload.get("user_id") or "") != expected_user:
        raise PermissionError("Effect-system preview token belongs to a different member")
    if str(payload.get("track_id") or "") != expected_track:
        raise PermissionError("Effect-system preview token belongs to a different track")
    if str(payload.get("fingerprint") or "").casefold() != expected_fingerprint:
        raise PermissionError("Effect-system graph changed after preview; preview the current graph again before apply")
    return {
        "consumed": True,
        "fingerprint": expected_fingerprint,
        "expires_at": expires_at,
        "server_authoritative": True,
        "one_time": True,
        "atomic_claim": True,
        "raw_token_persisted": False,
    }


__all__ = [
    "ISSUANCE_LOCK_STALE_SECONDS",
    "ISSUANCE_LOCK_WAIT_SECONDS",
    "MAX_ACTIVE_PREVIEW_TOKENS",
    "PREVIEW_TOKEN_TTL_SECONDS",
    "consume_effect_system_preview_token",
    "issue_effect_system_preview_token",
]
