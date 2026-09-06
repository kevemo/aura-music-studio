from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

PREVIEW_TOKEN_TTL_SECONDS = 15 * 60
MAX_ACTIVE_PREVIEW_TOKENS = 512
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _preview_root(project: Path) -> Path:
    project_root = project.resolve()
    root = (project_root / "work" / "effect_system_previews").resolve()
    if project_root not in root.parents:
        raise ValueError("Effect-system preview evidence path escapes project storage")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _token_path(project: Path, token: str) -> Path:
    normalized = str(token or "").strip().casefold()
    if not _TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError("Effect-system preview token is invalid")
    root = _preview_root(project).resolve()
    target = (root / f"{normalized}.json").resolve()
    if root not in target.parents:
        raise ValueError("Effect-system preview token path escapes project storage")
    return target


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
    for path in root.glob("*.json"):
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
    """Consume one preview proof exactly once and fail closed on any binding mismatch."""
    normalized_token = str(token or "").strip().casefold()
    path = _token_path(project, normalized_token)
    if not path.is_file():
        raise PermissionError("Effect-system preview token is missing, expired or already consumed")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PermissionError("Effect-system preview token could not be read") from exc
    finally:
        try:
            path.unlink(missing_ok=True)
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
        "token": normalized_token,
        "fingerprint": expected_fingerprint,
        "expires_at": expires_at,
        "server_authoritative": True,
        "one_time": True,
    }


__all__ = [
    "MAX_ACTIVE_PREVIEW_TOKENS",
    "PREVIEW_TOKEN_TTL_SECONDS",
    "consume_effect_system_preview_token",
    "issue_effect_system_preview_token",
]
