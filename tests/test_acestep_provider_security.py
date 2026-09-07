from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio import acestep_api as ace


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


def test_provider_url_rejects_cross_origin_credentials_and_fragments():
    client = ace.AceStepClient(base_url="https://ace.internal.example:8443", api_key="secret")
    assert client._provider_url("/v1/audio/test.wav") == "https://ace.internal.example:8443/v1/audio/test.wav"
    with pytest.raises(PermissionError):
        client._provider_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(PermissionError):
        client._provider_url("https://evil.example/audio.wav")
    with pytest.raises(ValueError):
        client._provider_url("https://user:secret@ace.internal.example:8443/audio.wav")
    with pytest.raises(ValueError):
        client._provider_url("https://ace.internal.example:8443/audio.wav#fragment")


def test_download_rejects_cross_origin_redirect_without_forwarding_auth(monkeypatch, tmp_path: Path):
    client = ace.AceStepClient(base_url="https://ace.example", api_key="top-secret")
    redirect = FakeResponse(status_code=302, headers={"location": "http://127.0.0.1/admin"})
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return redirect

    monkeypatch.setattr(client.session, "get", fake_get)
    output = tmp_path / "song.wav"
    with pytest.raises(PermissionError):
        client.download("/download/start", output)

    assert [url for url, _ in calls] == ["https://ace.example/download/start"]
    assert calls[0][1]["allow_redirects"] is False
    assert redirect.closed is True
    assert not output.exists()
    assert not list(tmp_path.glob(".*.part"))


def test_download_rejects_declared_oversize(monkeypatch, tmp_path: Path):
    client = ace.AceStepClient(base_url="https://ace.example")
    monkeypatch.setenv("AURA_ACESTEP_MAX_AUDIO_BYTES", "4096")
    response = FakeResponse(headers={"content-length": "4097"}, chunks=[b"x" * 4097])
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)

    output = tmp_path / "song.wav"
    with pytest.raises(ValueError, match="exceeded"):
        client.download("/audio.wav", output)
    assert response.closed is True
    assert not output.exists()
    assert not list(tmp_path.glob(".*.part"))


def test_download_rejects_streamed_oversize_and_preserves_existing_output(monkeypatch, tmp_path: Path):
    client = ace.AceStepClient(base_url="https://ace.example")
    monkeypatch.setenv("AURA_ACESTEP_MAX_AUDIO_BYTES", "4096")
    response = FakeResponse(chunks=[b"a" * 3000, b"b" * 2000])
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)

    output = tmp_path / "song.wav"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="exceeded"):
        client.download("/audio.wav", output)
    assert output.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".*.part"))


def test_download_accepts_bounded_same_origin_audio(monkeypatch, tmp_path: Path):
    client = ace.AceStepClient(base_url="https://ace.example:443", api_key="secret")
    monkeypatch.setenv("AURA_ACESTEP_MAX_AUDIO_BYTES", "8192")
    payload = b"RIFF" + (b"x" * 4996)
    response = FakeResponse(headers={"content-length": str(len(payload))}, chunks=[payload])
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return response

    monkeypatch.setattr(client.session, "get", fake_get)
    output = tmp_path / "song.wav"
    assert client.download("/v1/audio/test.wav", output) == output
    assert seen["url"] == "https://ace.example:443/v1/audio/test.wav"
    assert seen["allow_redirects"] is False
    assert output.read_bytes() == payload
    assert not list(tmp_path.glob(".*.part"))


def test_download_limit_has_hard_server_ceiling(monkeypatch):
    monkeypatch.setenv("AURA_ACESTEP_MAX_AUDIO_BYTES", str(10 * 1024 * 1024 * 1024))
    assert ace._audio_download_limit() == ace._HARD_MAX_AUDIO_BYTES


def test_generate_rejects_non_mapping_provider_result(monkeypatch, tmp_path: Path):
    client = ace.AceStepClient(base_url="https://ace.example")
    monkeypatch.setattr(client, "submit", lambda _request: "task-1")
    monkeypatch.setattr(client, "wait", lambda _task_id: ["https://evil.example/audio.wav"])
    with pytest.raises(RuntimeError, match="invalid result item"):
        client.generate(ace.AceStepRequest(prompt="test"), tmp_path)
