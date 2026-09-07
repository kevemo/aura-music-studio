from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aura_music_studio.load_probe import ProbeConfig, run_load_probe


class _HealthyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def test_probe_refuses_remote_target_without_explicit_operator_opt_in():
    with pytest.raises(PermissionError):
        ProbeConfig("https://example.com/health/live").validated()


def test_remote_probe_requires_https_even_when_explicitly_enabled():
    with pytest.raises(ValueError):
        ProbeConfig("http://example.com/health/live", allow_remote=True).validated()


def test_probe_has_hard_request_concurrency_and_timeout_caps():
    with pytest.raises(ValueError):
        ProbeConfig("http://127.0.0.1:8000", requests=501).validated()
    with pytest.raises(ValueError):
        ProbeConfig("http://127.0.0.1:8000", concurrency=21, requests=21).validated()
    with pytest.raises(ValueError):
        ProbeConfig("http://127.0.0.1:8000", timeout_seconds=16).validated()
    with pytest.raises(ValueError):
        ProbeConfig("http://127.0.0.1:8000", requests=2, concurrency=3).validated()


def test_local_bounded_probe_reports_success_and_latency_without_external_traffic():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = run_load_probe(
            ProbeConfig(
                f"http://127.0.0.1:{server.server_port}/health/live",
                requests=30,
                concurrency=5,
                timeout_seconds=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert report["ok"] is True
    assert report["request_count"] == 30
    assert report["success_count"] == 30
    assert report["error_count"] == 0
    assert report["bounded"] is True
    assert report["max_request_cap"] == 500
    assert report["max_concurrency_cap"] == 20
    assert report["latency_ms"]["p95"] >= report["latency_ms"]["min"]
