from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio import yue_remote_command as yue


class FakeResponse:
    def __init__(self, *, status_code: int = 200, headers: dict | None = None, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_safe_provider_url_rejects_cross_origin_and_credentials():
    base = "https://yue.internal.example:8443"
    assert yue._safe_provider_url(base, "/v1/audio/job-1") == "https://yue.internal.example:8443/v1/audio/job-1"
    with pytest.raises(PermissionError):
        yue._safe_provider_url(base, "https://169.254.169.254/latest/meta-data/")
    with pytest.raises(PermissionError):
        yue._safe_provider_url(base, "https://evil.example/audio.wav")
    with pytest.raises(ValueError):
        yue._safe_provider_url(base, "https://user:secret@yue.internal.example:8443/audio.wav")


def test_download_rejects_cross_origin_redirect(monkeypatch, tmp_path: Path):
    redirect = FakeResponse(status_code=302, headers={"location": "http://127.0.0.1/admin"})
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return redirect

    monkeypatch.setattr(yue.requests, "get", fake_get)
    output = tmp_path / "song.wav"
    with pytest.raises(PermissionError):
        yue._download_provider_audio(base="https://yue.example", audio_url="/download/start", output=output, headers={})

    assert calls == ["https://yue.example/download/start"]
    assert redirect.closed is True
    assert not output.exists()
    assert not (tmp_path / "song.wav.part").exists()


def test_download_rejects_declared_oversize(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AURA_YUE_MAX_AUDIO_BYTES", "4096")
    response = FakeResponse(headers={"content-length": "4097"}, chunks=[b"x" * 4097])
    monkeypatch.setattr(yue.requests, "get", lambda *args, **kwargs: response)
    output = tmp_path / "song.wav"
    with pytest.raises(ValueError, match="exceeded"):
        yue._download_provider_audio(base="https://yue.example", audio_url="/audio.wav", output=output, headers={})
    assert response.closed is True
    assert not output.exists()
    assert not (tmp_path / "song.wav.part").exists()


def test_download_rejects_streamed_oversize_and_removes_partial(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AURA_YUE_MAX_AUDIO_BYTES", "4096")
    response = FakeResponse(chunks=[b"a" * 3000, b"b" * 2000])
    monkeypatch.setattr(yue.requests, "get", lambda *args, **kwargs: response)
    output = tmp_path / "song.wav"
    with pytest.raises(ValueError, match="exceeded"):
        yue._download_provider_audio(base="https://yue.example", audio_url="/audio.wav", output=output, headers={})
    assert not output.exists()
    assert not (tmp_path / "song.wav.part").exists()


def test_download_accepts_bounded_same_origin_audio(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AURA_YUE_MAX_AUDIO_BYTES", "8192")
    payload = b"RIFF" + (b"x" * 4996)
    response = FakeResponse(headers={"content-length": str(len(payload))}, chunks=[payload])
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return response

    monkeypatch.setattr(yue.requests, "get", fake_get)
    output = tmp_path / "song.wav"
    yue._download_provider_audio(
        base="https://yue.example:443",
        audio_url="/v1/audio/job_123",
        output=output,
        headers={"Authorization": "Bearer test"},
    )

    assert seen["url"] == "https://yue.example:443/v1/audio/job_123"
    assert seen["allow_redirects"] is False
    assert output.read_bytes() == payload
    assert not (tmp_path / "song.wav.part").exists()


def test_download_limit_is_bounded_even_if_operator_config_is_huge(monkeypatch):
    monkeypatch.setenv("AURA_YUE_MAX_AUDIO_BYTES", str(10 * 1024 * 1024 * 1024))
    assert yue._audio_download_limit() == yue._MAX_CONFIGURED_AUDIO_BYTES


def test_job_id_contract_rejects_path_control_characters():
    assert yue._JOB_ID_RE.fullmatch("job_123-abc")
    assert not yue._JOB_ID_RE.fullmatch("../metadata")
    assert not yue._JOB_ID_RE.fullmatch("job/../../admin")
    assert not yue._JOB_ID_RE.fullmatch("job?next=/admin")
