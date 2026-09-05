from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from aura_music_studio import api
from aura_music_studio import upload_security as uploads


class FakeUpload:
    def __init__(self, payload: bytes, *, filename: str = "upload.bin", declared_size=None):
        self.filename = filename
        self.size = declared_size
        self._payload = payload
        self._offset = 0
        self.read_calls = 0

    async def read(self, size: int) -> bytes:
        self.read_calls += 1
        if self._offset >= len(self._payload):
            return b""
        end = min(len(self._payload), self._offset + size)
        chunk = self._payload[self._offset:end]
        self._offset = end
        return chunk


def test_safe_upload_filename_strips_paths_and_rejects_special_components():
    assert uploads.safe_upload_filename("../../voice.wav", default="voice.wav") == "voice.wav"
    assert uploads.safe_upload_filename(r"C:\\temp\\sample.mp3", default="voice.wav") == "sample.mp3"
    assert uploads.safe_upload_filename(None, default="voice.wav") == "voice.wav"

    with pytest.raises(ValueError):
        uploads.safe_upload_filename("..", default="voice.wav")
    with pytest.raises(ValueError):
        uploads.safe_upload_filename("bad\x00name.wav", default="voice.wav")


def test_upload_limits_are_hard_bounded(monkeypatch):
    monkeypatch.setenv("AURA_ASSET_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024 * 1024))
    monkeypatch.setenv("AURA_VOICE_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024 * 1024))
    assert uploads.asset_upload_limit() == uploads._HARD_MAX_ASSET_UPLOAD_BYTES
    assert uploads.voice_upload_limit() == uploads._HARD_MAX_VOICE_UPLOAD_BYTES


def test_declared_oversize_fails_before_streaming(tmp_path: Path):
    target = tmp_path / "asset.txt"
    upload = FakeUpload(b"small", filename="asset.txt", declared_size=5000)
    with pytest.raises(uploads.UploadTooLargeError):
        asyncio.run(uploads.save_bounded_upload(upload, target, max_bytes=4096))
    assert upload.read_calls == 0
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))


def test_streamed_oversize_preserves_existing_file_and_cleans_partial(tmp_path: Path):
    target = tmp_path / "asset.txt"
    target.write_bytes(b"existing")
    upload = FakeUpload(b"x" * 5000, filename="asset.txt")

    with pytest.raises(uploads.UploadTooLargeError):
        asyncio.run(uploads.save_bounded_upload(upload, target, max_bytes=4096))

    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".*.part"))


def test_bounded_upload_promotes_atomically(tmp_path: Path):
    target = tmp_path / "asset.txt"
    payload = b"x" * 4096
    upload = FakeUpload(payload, filename="asset.txt")
    written = asyncio.run(uploads.save_bounded_upload(upload, target, max_bytes=4096))
    assert written == len(payload)
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob(".*.part"))


def test_asset_endpoint_returns_413_and_leaves_no_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)
    monkeypatch.setenv("AURA_ASSET_UPLOAD_MAX_BYTES", "4096")
    upload = FakeUpload(b"x" * 5000, filename="notes.txt")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            api.upload_asset(
                "project",
                file=upload,
                kind="auto",
                rights_basis="user_owned_or_licensed",
                attestation="I confirm I have the right to use this material in this project.",
                tags="",
            )
        )
    assert caught.value.status_code == 413
    incoming = tmp_path / "input" / "uploads"
    assert not incoming.exists() or not list(incoming.iterdir())


def test_asset_ingest_failure_removes_temporary_upload(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)

    def fail_ingest(self, *args, **kwargs):
        raise RuntimeError("malformed media")

    monkeypatch.setattr(api.AssetLibrary, "ingest", fail_ingest)
    upload = FakeUpload(b"valid-enough-for-stream" * 300, filename="notes.txt")

    with pytest.raises(RuntimeError, match="malformed media"):
        asyncio.run(
            api.upload_asset(
                "project",
                file=upload,
                kind="auto",
                rights_basis="user_owned_or_licensed",
                attestation="I confirm I have the right to use this material in this project.",
                tags="",
            )
        )
    incoming = tmp_path / "input" / "uploads"
    assert not incoming.exists() or not list(incoming.iterdir())


def test_voice_endpoint_returns_413_and_leaves_no_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)
    monkeypatch.setenv("AURA_VOICE_UPLOAD_MAX_BYTES", "4096")
    upload = FakeUpload(b"x" * 5000, filename="voice.wav")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            api.new_voice_profile(
                "project",
                name="Test voice",
                owner_label="Test owner",
                consent_statement="I consent to this voice profile for this test.",
                reference=upload,
            )
        )
    assert caught.value.status_code == 413
    voice_dir = tmp_path / "input" / "voice_profiles"
    assert not voice_dir.exists() or not list(voice_dir.iterdir())


def test_failed_voice_analysis_removes_uploaded_reference(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)
    monkeypatch.setattr(api, "create_voice_profile", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad audio")))
    upload = FakeUpload(b"RIFF" + b"x" * 5000, filename="voice.wav")

    with pytest.raises(RuntimeError, match="bad audio"):
        asyncio.run(
            api.new_voice_profile(
                "project",
                name="Test voice",
                owner_label="Test owner",
                consent_statement="I consent to this voice profile for this test.",
                reference=upload,
            )
        )
    voice_dir = tmp_path / "input" / "voice_profiles"
    assert not voice_dir.exists() or not list(voice_dir.iterdir())
