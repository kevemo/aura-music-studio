from __future__ import annotations

import urllib.error

import pytest

import aura_music_studio.load_probe as load_probe
from aura_music_studio.load_probe import ProbeConfig, run_load_probe


def test_probe_defaults_to_loopback_and_rejects_implicit_remote_target():
    with pytest.raises(PermissionError, match="explicit allow_remote"):
        ProbeConfig("https://example.com/health/live").validated()


def test_remote_probe_requires_https_even_when_explicitly_authorized():
    with pytest.raises(ValueError, match="require HTTPS"):
        ProbeConfig("http://example.com/health/live", allow_remote=True).validated()


def test_probe_rejects_embedded_credentials_and_fragments():
    with pytest.raises(ValueError, match="embedded credentials"):
        ProbeConfig("http://user:pass@127.0.0.1:8000/health/live").validated()
    with pytest.raises(ValueError, match="fragment"):
        ProbeConfig("http://127.0.0.1:8000/health/live#ignored").validated()


def test_probe_enforces_bounded_requests_concurrency_and_thresholds():
    with pytest.raises(ValueError, match="requests must be between"):
        ProbeConfig("http://127.0.0.1/", requests=501).validated()
    with pytest.raises(ValueError, match="concurrency must be between"):
        ProbeConfig("http://127.0.0.1/", concurrency=21).validated()
    with pytest.raises(ValueError, match="minimum_success_ratio"):
        ProbeConfig("http://127.0.0.1/", minimum_success_ratio=1.01).validated()
    with pytest.raises(ValueError, match="maximum_p95_ms"):
        ProbeConfig("http://127.0.0.1/", maximum_p95_ms=0.5).validated()


def test_probe_records_transport_failures_instead_of_aborting(monkeypatch):
    def fail(_request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(load_probe.urllib.request, "urlopen", fail)
    report = run_load_probe(
        ProbeConfig(
            "http://127.0.0.1:8000/health/live",
            requests=4,
            concurrency=2,
            timeout_seconds=1,
        )
    )

    assert report["ok"] is False
    assert report["success_count"] == 0
    assert report["error_count"] == 4
    assert report["transport_error_count"] == 4
    assert report["status_counts"] == {"transport_error": 4}
    assert report["transport_error_types"] == {"URLError": 4}
    assert report["thresholds"]["success_threshold_passed"] is False


def test_probe_applies_success_and_latency_thresholds(monkeypatch):
    samples = iter(
        [
            (200, 10.0, None),
            (200, 20.0, None),
            (503, 30.0, None),
            (200, 40.0, None),
        ]
    )
    monkeypatch.setattr(load_probe, "_one", lambda _url, _timeout: next(samples))

    report = run_load_probe(
        ProbeConfig(
            "http://127.0.0.1:8000/health/live",
            requests=4,
            concurrency=1,
            minimum_success_ratio=0.75,
            maximum_p95_ms=40.0,
        )
    )

    assert report["ok"] is True
    assert report["success_ratio"] == 0.75
    assert report["latency_ms"]["p95"] == 40.0
    assert report["latency_ms"]["p99"] == 40.0
    assert report["status_counts"] == {"200": 3, "503": 1}
    assert report["thresholds"]["success_threshold_passed"] is True
    assert report["thresholds"]["latency_threshold_passed"] is True
    assert report["evidence_scope"] == "bounded_http_smoke_not_production_soak_or_capacity_proof"


def test_probe_fails_latency_threshold_even_when_every_request_succeeds(monkeypatch):
    monkeypatch.setattr(load_probe, "_one", lambda _url, _timeout: (200, 250.0, None))
    report = run_load_probe(
        ProbeConfig(
            "http://127.0.0.1/health/live",
            requests=3,
            concurrency=1,
            maximum_p95_ms=200.0,
        )
    )

    assert report["success_count"] == 3
    assert report["ok"] is False
    assert report["thresholds"]["success_threshold_passed"] is True
    assert report["thresholds"]["latency_threshold_passed"] is False
