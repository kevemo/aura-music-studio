from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


_DEFAULT_ASSET_UPLOAD_BYTES = 512 * 1024 * 1024
_DEFAULT_VOICE_UPLOAD_BYTES = 128 * 1024 * 1024
_HARD_MAX_ASSET_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
_HARD_MAX_VOICE_UPLOAD_BYTES = 512 * 1024 * 1024
_MIN_UPLOAD_BYTES = 4096
_CHUNK_BYTES = 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[^\x00-\x1f\x7f]{1,180}$")


class UploadTooLargeError(ValueError):
    pass


def _configured_limit(env_name: str, default: int, hard_max: int) -> int:
    raw = (os.getenv(env_name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if value < _MIN_UPLOAD_BYTES:
        raise ValueError(f"{env_name} must be at least {_MIN_UPLOAD_BYTES} bytes")
    return min(value, hard_max)


def asset_upload_limit() -> int:
    return _configured_limit(
        "AURA_ASSET_UPLOAD_MAX_BYTES",
        _DEFAULT_ASSET_UPLOAD_BYTES,
        _HARD_MAX_ASSET_UPLOAD_BYTES,
    )


def voice_upload_limit() -> int:
    return _configured_limit(
        "AURA_VOICE_UPLOAD_MAX_BYTES",
        _DEFAULT_VOICE_UPLOAD_BYTES,
        _HARD_MAX_VOICE_UPLOAD_BYTES,
    )


def safe_upload_filename(value: str | None, *, default: str) -> str:
    """Return one cross-platform filename component, never a caller-controlled path."""

    raw = str(value or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip() or default
    if name in {".", ".."} or not _SAFE_FILENAME.fullmatch(name):
        raise ValueError("Upload filename is invalid")
    return name


async def save_bounded_upload(upload: UploadFile, destination: Path, *, max_bytes: int) -> int:
    """Stream one upload to a random sibling temporary file and atomically promote it.

    The streamed byte count is authoritative. Any declared size is only an early-rejection
    hint and never replaces the bounded read. Partial files are removed on every failure.
    """

    if max_bytes < _MIN_UPLOAD_BYTES:
        raise ValueError("Upload byte limit is invalid")

    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and declared > max_bytes:
        raise UploadTooLargeError("Upload exceeds the configured size limit")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    total = 0
    try:
        with partial.open("xb") as handle:
            try:
                os.chmod(partial, 0o600)
            except OSError:
                pass
            while True:
                chunk = await upload.read(_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError("Upload exceeds the configured size limit")
                handle.write(chunk)
        if total == 0:
            raise ValueError("Upload is empty")
        partial.replace(destination)
        return total
    except Exception:
        partial.unlink(missing_ok=True)
        raise


__all__ = [
    "UploadTooLargeError",
    "asset_upload_limit",
    "safe_upload_filename",
    "save_bounded_upload",
    "voice_upload_limit",
]
