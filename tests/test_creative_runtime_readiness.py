from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aura_music_studio.creative_runtime_readiness import (
    RuntimeReadinessError,
    creative_runtime_workload_ready,
    load_creative_runtime_evidence,
)
from aura_music_studio.renderers import ExternalCommandRenderer


def _evidence(*, engine: str = "local_acestep", checked_at: datetime | None = None, **overrides):
    payload = {
        "schema_version": 1,
        "engine": engine,
        "model_id": "ace-step-1.5",
        "model_digest": "sha256:" + "a" * 64,
        "runtime_id": "creative-gpu-worker-01",
        "runtime_digest": "sha256:" + "b" * 64,
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
        "healthy": True,
        "inference_verified": True,
        "model_loaded": True,
        "storage_ready": True,
        "capacity_ready": True,
        "recovery_ready": True,
        "gpu_required": True,
        "gpu_available": True,
        "cuda_available": True,
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload):
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _renderer() -> ExternalCommandRenderer:
    return ExternalCommandRenderer(
        "local_acestep",
        "AURA_LOCAL_RENDER_CMD",
        "neural_master_local.wav",
        "AURA_LOCAL_RENDER_READINESS_FILE",
    )


def test_fresh_complete_evidence_is_workload_ready(tmp_path):
    path = _write(tmp_path, _evidence())
    evidence = load_creative_runtime_evidence(path, expected_engine="local_acestep")
    assert evidence.engine == "local_acestep"
    assert evidence.model_id == "ace-step-1.5"
    assert evidence.provenance_metadata()["runtime_workload_ready"] is True
    assert evidence.provenance_metadata()["runtime_production_evidenced"] is False
    assert creative_runtime_workload_ready(path, expected_engine="local_acestep") is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("healthy", False),
        ("inference_verified", False),
        ("model_loaded", False),
        ("storage_ready", False),
        ("capacity_ready", False),
        ("recovery_ready", False),
    ],
)
def test_required_workload_signal_false_fails_closed(tmp_path, field, value):
    path = _write(tmp_path, _evidence(**{field: value}))
    with pytest.raises(RuntimeReadinessError, match="not workload-ready"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")


def test_gpu_runtime_requires_gpu_and_cuda_evidence(tmp_path):
    path = _write(tmp_path, _evidence(gpu_available=False))
    with pytest.raises(RuntimeReadinessError, match="GPU/CUDA"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")

    path = _write(tmp_path, _evidence(cuda_available=False))
    with pytest.raises(RuntimeReadinessError, match="GPU/CUDA"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")


def test_cpu_runtime_does_not_invent_gpu_requirement(tmp_path):
    path = _write(
        tmp_path,
        _evidence(gpu_required=False, gpu_available=False, cuda_available=False),
    )
    assert load_creative_runtime_evidence(path, expected_engine="local_acestep").gpu_required is False


def test_engine_mismatch_fails_closed(tmp_path):
    path = _write(tmp_path, _evidence(engine="yue"))
    with pytest.raises(RuntimeReadinessError, match="engine mismatch"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_digest", "latest", "model_digest"),
        ("runtime_digest", "sha256:not-a-digest", "runtime_digest"),
        ("model_id", "", "model_id"),
        ("runtime_id", "", "runtime_id"),
    ],
)
def test_identity_and_digest_evidence_is_mandatory(tmp_path, field, value, message):
    path = _write(tmp_path, _evidence(**{field: value}))
    with pytest.raises(RuntimeReadinessError, match=message):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")


def test_stale_or_future_evidence_fails_closed(tmp_path):
    now = datetime.now(timezone.utc)
    stale = _write(tmp_path, _evidence(checked_at=now - timedelta(minutes=10)))
    with pytest.raises(RuntimeReadinessError, match="stale"):
        load_creative_runtime_evidence(stale, expected_engine="local_acestep", now=now)

    future = _write(tmp_path, _evidence(checked_at=now + timedelta(minutes=2)))
    with pytest.raises(RuntimeReadinessError, match="future"):
        load_creative_runtime_evidence(future, expected_engine="local_acestep", now=now)


def test_malformed_missing_and_oversize_evidence_are_unavailable(tmp_path):
    missing = tmp_path / "missing.json"
    assert creative_runtime_workload_ready(missing, expected_engine="local_acestep") is False

    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    assert creative_runtime_workload_ready(malformed, expected_engine="local_acestep") is False

    oversized = tmp_path / "oversized.json"
    oversized.write_text("{" + "x" * (65 * 1024) + "}", encoding="utf-8")
    assert creative_runtime_workload_ready(oversized, expected_engine="local_acestep") is False


def test_max_age_configuration_is_bounded(tmp_path, monkeypatch):
    path = _write(tmp_path, _evidence())
    monkeypatch.setenv("AURA_CREATIVE_RUNTIME_MAX_EVIDENCE_AGE_SECONDS", "5")
    with pytest.raises(RuntimeReadinessError, match="between 30 and 3600"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")

    monkeypatch.setenv("AURA_CREATIVE_RUNTIME_MAX_EVIDENCE_AGE_SECONDS", "not-an-int")
    with pytest.raises(RuntimeReadinessError, match="must be an integer"):
        load_creative_runtime_evidence(path, expected_engine="local_acestep")


def test_command_only_is_configured_but_not_workload_ready(monkeypatch):
    renderer = _renderer()
    monkeypatch.setenv("AURA_LOCAL_RENDER_CMD", sys.executable)
    monkeypatch.delenv("AURA_LOCAL_RENDER_READINESS_FILE", raising=False)
    assert renderer.configured() is True
    assert renderer.available() is False


def test_valid_evidence_and_executable_make_renderer_available(tmp_path, monkeypatch):
    evidence_path = _write(tmp_path, _evidence())
    renderer = _renderer()
    monkeypatch.setenv("AURA_LOCAL_RENDER_CMD", sys.executable)
    monkeypatch.setenv("AURA_LOCAL_RENDER_READINESS_FILE", str(evidence_path))
    assert renderer.available() is True


def test_unresolvable_executable_never_reports_available(tmp_path, monkeypatch):
    evidence_path = _write(tmp_path, _evidence())
    renderer = _renderer()
    monkeypatch.setenv("AURA_LOCAL_RENDER_CMD", "definitely-not-an-aura-runtime-executable")
    monkeypatch.setenv("AURA_LOCAL_RENDER_READINESS_FILE", str(evidence_path))
    assert renderer.available() is False


def test_readiness_is_rechecked_before_subprocess(tmp_path, monkeypatch):
    evidence_path = _write(tmp_path, _evidence())
    renderer = _renderer()
    monkeypatch.setenv("AURA_LOCAL_RENDER_CMD", sys.executable)
    monkeypatch.setenv("AURA_LOCAL_RENDER_READINESS_FILE", str(evidence_path))
    assert renderer.available() is True

    evidence_path.write_text(json.dumps(_evidence(healthy=False)), encoding="utf-8")
    calls = []
    monkeypatch.setattr("aura_music_studio.renderers.subprocess.run", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(RuntimeReadinessError, match="not workload-ready"):
        renderer.render(object(), object(), object())
    assert calls == []


def test_successful_local_render_records_runtime_provenance(tmp_path, monkeypatch):
    evidence_path = _write(tmp_path, _evidence())
    renderer = _renderer()
    monkeypatch.setenv("AURA_LOCAL_RENDER_CMD", sys.executable)
    monkeypatch.setenv("AURA_LOCAL_RENDER_READINESS_FILE", str(evidence_path))

    root = tmp_path / "project"
    work = root / "work"
    work.mkdir(parents=True)

    class Workspace:
        def __init__(self):
            self.root = root
            self.work_dir = work

        @staticmethod
        def resolve_asset(_value):
            return None

    manifest = SimpleNamespace(
        reference_audio=None,
        lyrics_file=None,
        target_duration_seconds=30,
        total_measures=None,
        renderer=SimpleNamespace(duration_limit_seconds=180),
    )
    plan = SimpleNamespace(
        render_prompt="test prompt",
        negative_prompt="",
        tempo_bpm=120.0,
        key="C",
        meter="4/4",
    )

    def fake_run(command, *, cwd, env, check):
        assert command == [sys.executable]
        assert cwd == root
        assert check is True
        assert env["AURA_RUNTIME_MODEL_DIGEST"] == "sha256:" + "a" * 64
        assert env["AURA_RUNTIME_DIGEST"] == "sha256:" + "b" * 64
        (work / "neural_master_local.wav").write_bytes(b"runtime-output")

    monkeypatch.setattr("aura_music_studio.renderers.subprocess.run", fake_run)
    result = renderer.render(Workspace(), manifest, plan)
    assert result.renderer == "local_acestep"
    assert result.audio_path.read_bytes() == b"runtime-output"
    assert result.metadata["runtime_model_id"] == "ace-step-1.5"
    assert result.metadata["runtime_workload_ready"] is True
    assert result.metadata["runtime_production_evidenced"] is False
