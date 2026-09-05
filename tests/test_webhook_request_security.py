from starlette.requests import Request

from aura_music_studio.security import _webhook_request_admission


def _request(*, path: str = "/billing/stripe/webhook", method: str = "POST", headers: dict[str, str] | None = None):
    raw_headers = [
        (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("203.0.113.10", 443),
            "server": ("command.example", 443),
        }
    )


def test_webhook_requires_declared_bounded_content_length(monkeypatch):
    monkeypatch.delenv("LSS_WEBHOOK_MAX_BYTES", raising=False)
    rejection = _webhook_request_admission(_request())
    assert rejection is not None
    assert rejection.status_code == 411


def test_oversized_webhook_is_rejected_before_route_body_read(monkeypatch):
    monkeypatch.setenv("LSS_WEBHOOK_MAX_BYTES", "4096")
    rejection = _webhook_request_admission(
        _request(headers={"Content-Length": "4097", "Content-Type": "application/json"})
    )
    assert rejection is not None
    assert rejection.status_code == 413


def test_webhook_at_configured_limit_is_admitted(monkeypatch):
    monkeypatch.setenv("LSS_WEBHOOK_MAX_BYTES", "4096")
    rejection = _webhook_request_admission(
        _request(headers={"Content-Length": "4096", "Content-Type": "application/json"})
    )
    assert rejection is None


def test_compressed_webhook_body_is_rejected(monkeypatch):
    monkeypatch.delenv("LSS_WEBHOOK_MAX_BYTES", raising=False)
    rejection = _webhook_request_admission(
        _request(headers={"Content-Length": "100", "Content-Encoding": "gzip"})
    )
    assert rejection is not None
    assert rejection.status_code == 415


def test_invalid_webhook_limit_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("LSS_WEBHOOK_MAX_BYTES", "not-a-number")
    rejection = _webhook_request_admission(
        _request(headers={"Content-Length": "100"})
    )
    assert rejection is not None
    assert rejection.status_code == 503


def test_non_webhook_or_non_post_request_is_not_subject_to_webhook_body_gate(monkeypatch):
    monkeypatch.setenv("LSS_WEBHOOK_MAX_BYTES", "4096")
    assert _webhook_request_admission(
        _request(path="/api/game-forge/upload", headers={"Content-Length": "9999999"})
    ) is None
    assert _webhook_request_admission(
        _request(path="/billing/stripe/webhook", method="GET", headers={})
    ) is None
