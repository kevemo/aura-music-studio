from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.acestep_api import AceStepClient, AceStepRequest
from aura_music_studio.models import RendererConfig
from aura_music_studio.renderer_runtime import probe_real_audio, renderer_runtime_status, require_live_renderer


def _wav(path: Path, seconds: float = 1.25, sr: int = 48000) -> Path:
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    audio = (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(path, audio, sr, subtype="PCM_24")
    return path


def test_default_renderer_order_is_self_host_first_and_public_spaces_are_opt_in():
    preferred = RendererConfig().preferred
    assert preferred[:4] == ["acestep_api", "local_acestep", "muser", "yue"]
    assert "acestep_space" not in preferred
    assert preferred.index("yue") < preferred.index("deapi")
    assert preferred.index("yue") < preferred.index("eleven_music")
    assert preferred.index("yue") < preferred.index("mureka")


def test_real_audio_probe_accepts_waveform_and_rejects_symbolic(tmp_path: Path):
    good = _wav(tmp_path / "real.wav")
    probe = probe_real_audio(good)
    assert probe.valid is True
    assert probe.sample_rate == 48000
    assert probe.duration_seconds and probe.duration_seconds >= 1.0

    midi = tmp_path / "guide.mid"
    midi.write_bytes(b"MThd\x00\x00\x00\x06")
    rejected = probe_real_audio(midi)
    assert rejected.valid is False
    assert "symbolic" in (rejected.error or "").lower()


def test_renderer_preflight_can_be_strict_without_exposing_internal_urls(monkeypatch):
    for key in (
        "AURA_ACESTEP_API_URL", "AURA_YUE_API_URL", "AURA_LOCAL_RENDER_CMD", "AURA_MUSER_CMD",
        "AURA_DIFFRHYTHM_CMD", "AURA_AUDIOCRAFT_CMD", "AURA_STABLE_AUDIO_CMD", "DEAPI_API_KEY",
        "ELEVENLABS_API_KEY", "MUREKA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AURA_REQUIRE_LIVE_RENDERER", "true")
    status = renderer_runtime_status()
    assert status["final_master_renderer_ready"] is False
    assert all("url" not in engine or engine.get("internal_url_exposed") is False for engine in status["engines"])
    with pytest.raises(RuntimeError, match="No live real-audio music renderer"):
        require_live_renderer()


def test_acestep_client_uses_documented_async_contract(monkeypatch, tmp_path: Path):
    calls = []

    class Response:
        def __init__(self, payload=None, content=b"", *, status_code=200, headers=None):
            self._payload = payload
            self.content = content
            self.ok = True
            self.status_code = status_code
            self.headers = dict(headers or {})
            self.closed = False

        def close(self):
            self.closed = True

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def iter_content(self, _size):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
            return False

    client = AceStepClient(base_url="http://ace-step:8001", api_key="secret", timeout=5)

    def post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        if url.endswith("/release_task"):
            return Response({"code": 200, "data": {"task_id": "abc123"}})
        if url.endswith("/query_result"):
            return Response({"data": [{"task_id": "abc123", "status": 1, "result": json.dumps([{"file": "/v1/audio?path=test.wav"}])}]})
        raise AssertionError(url)

    real = _wav(tmp_path / "generated.wav")

    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        if "/v1/audio" in url:
            payload = real.read_bytes()
            return Response(content=payload, headers={"content-length": str(len(payload))})
        return Response({"data": {"models": []}})

    monkeypatch.setattr(client.session, "post", post)
    monkeypatch.setattr(client.session, "get", get)
    outputs = client.generate(
        AceStepRequest(prompt="uplifting pop", lyrics="[Verse]\nHello", task_type="text2music", audio_format="wav"),
        tmp_path / "out",
    )
    assert outputs and outputs[0].is_file()
    assert any(url.endswith("/release_task") for method, url, _ in calls if method == "POST")
    assert any(url.endswith("/query_result") for method, url, _ in calls if method == "POST")
    download_calls = [kwargs for method, url, kwargs in calls if method == "GET" and "/v1/audio" in url]
    assert download_calls and download_calls[0]["allow_redirects"] is False
    assert probe_real_audio(outputs[0]).valid is True


def test_yue_command_bridge_submits_polls_and_downloads(monkeypatch, tmp_path: Path):
    from aura_music_studio import yue_remote_command

    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("[Verse]\nHello world\n[Chorus]\nSing again", encoding="utf-8")
    output = tmp_path / "yue.wav"
    source = _wav(tmp_path / "source.wav")
    monkeypatch.setenv("AURA_YUE_API_URL", "http://yue:8011")
    monkeypatch.setenv("AURA_LYRICS", str(lyrics))
    monkeypatch.setenv("AURA_PROMPT", "uplifting pop female vocal bright guitar")
    monkeypatch.setenv("AURA_OUTPUT", str(output))
    monkeypatch.setenv("AURA_YUE_MAX_SEGMENTS", "2")

    class Response:
        def __init__(self, payload=None, content=b"", *, status_code=200, headers=None):
            self._payload = payload
            self._content = content
            self.status_code = status_code
            self.headers = dict(headers or {})
            self.closed = False

        def close(self):
            self.closed = True

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def iter_content(self, _size):
            yield self._content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
            return False

    submitted = {}
    download_kwargs = {}

    def post(url, **kwargs):
        submitted.update(kwargs.get("json") or {})
        return Response({"job_id": "job1", "status": "queued"})

    def get(url, **kwargs):
        if url.endswith("/v1/jobs/job1"):
            return Response({"job_id": "job1", "status": "completed", "audio_url": "/v1/audio/job1"})
        if url.endswith("/v1/audio/job1"):
            download_kwargs.update(kwargs)
            payload = source.read_bytes()
            return Response(content=payload, headers={"content-length": str(len(payload))})
        raise AssertionError(url)

    monkeypatch.setattr(yue_remote_command.requests, "post", post)
    monkeypatch.setattr(yue_remote_command.requests, "get", get)
    assert yue_remote_command.main() == 0
    assert submitted["segments"] == 2
    assert submitted["stage1_model"].startswith("m-a-p/YuE-s1")
    assert download_kwargs["allow_redirects"] is False
    assert output.is_file()
    assert probe_real_audio(output).valid is True
