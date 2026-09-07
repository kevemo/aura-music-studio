from pathlib import Path

from starlette.requests import Request

from aura_music_studio import owner_auth, owner_backup_portal, owner_compute_portal


def _request(token: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((b"cookie", f"lss_admin_session={token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/owner/test",
            "raw_path": b"/owner/test",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "owner-auth.sqlite3"))
    monkeypatch.setenv("LSS_ADMIN_KEY", "deployment-owner-key")
    owner_auth._sessions = None


def test_backup_portal_accepts_random_opaque_owner_session(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    token = owner_auth.sessions().create()
    assert token != "deployment-owner-key"
    assert owner_backup_portal._authorized(_request(token)) is True
    assert owner_backup_portal._authorized(_request()) is False


def test_compute_portal_accepts_random_opaque_owner_session(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    token = owner_auth.sessions().create()
    assert token != "deployment-owner-key"
    assert owner_compute_portal._authorized(_request(token)) is True
    assert owner_compute_portal._authorized(_request()) is False


def test_backup_and_compute_portals_no_longer_compare_deployment_admin_key_directly():
    backup_source = Path("aura_music_studio/owner_backup_portal.py").read_text(encoding="utf-8")
    compute_source = Path("aura_music_studio/owner_compute_portal.py").read_text(encoding="utf-8")
    for source in (backup_source, compute_source):
        assert "owner_authorized(request)" in source
        assert 'os.getenv("LSS_ADMIN_KEY")' not in source
        assert "compare_digest" not in source
