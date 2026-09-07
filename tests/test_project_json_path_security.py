from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.security as security


def test_project_name_from_path_is_bounded_to_project_routes():
    assert security._project_name_from_path("/projects/demo/master") == "demo"
    assert security._project_name_from_path("/projects/demo%20song/files") == "demo song"
    assert security._project_name_from_path("/owner/projects/demo") is None
    assert security._project_name_from_path("/projects/") is None


def test_safe_public_project_value_recursively_confines_paths(tmp_path: Path):
    project = (tmp_path / "member" / "demo").resolve()
    inside = project / "output" / "mix.wav"
    outside = (tmp_path / "host-secret" / "token.txt").resolve()
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"audio")
    outside.parent.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")

    payload = {
        "path": str(inside),
        "nested": {"stems": [str(inside), "output/already-public.wav"]},
        "outside": str(outside),
        "url": "https://example.com/file.wav",
    }

    assert security._safe_public_project_value(payload, project) == {
        "path": "output/mix.wav",
        "nested": {"stems": ["output/mix.wav", "output/already-public.wav"]},
        "outside": "[redacted-host-path]",
        "url": "https://example.com/file.wav",
    }


def test_security_middleware_scrubs_project_json_response(monkeypatch, tmp_path: Path):
    project = (tmp_path / "demo").resolve()
    output = project / "output" / "Aura_Session_Mix.wav"
    outside = (tmp_path / "server" / "private.db").resolve()
    output.parent.mkdir(parents=True)
    output.write_bytes(b"mix")
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"db")

    def fake_project_path(name: str, *, must_exist: bool = False):
        assert name == "demo"
        assert must_exist is True
        return project

    monkeypatch.setattr(security, "project_path", fake_project_path)

    app = FastAPI()
    app.add_middleware(security.StudioSecurityMiddleware)

    @app.get("/projects/demo/legacy")
    def legacy_payload():
        return {
            "path": str(output),
            "details": {"outside": str(outside)},
            "kept": "output/public.wav",
        }

    response = TestClient(app).get("/projects/demo/legacy")

    assert response.status_code == 200
    assert response.json() == {
        "path": "output/Aura_Session_Mix.wav",
        "details": {"outside": "[redacted-host-path]"},
        "kept": "output/public.wav",
    }
    assert str(tmp_path) not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_security_middleware_does_not_rewrite_non_project_json(monkeypatch, tmp_path: Path):
    absolute = str((tmp_path / "diagnostic.log").resolve())

    def fail_project_lookup(*_args, **_kwargs):
        raise AssertionError("non-project routes must not resolve project storage")

    monkeypatch.setattr(security, "project_path", fail_project_lookup)

    app = FastAPI()
    app.add_middleware(security.StudioSecurityMiddleware)

    @app.get("/health-test")
    def health_test():
        return {"diagnostic": absolute}

    response = TestClient(app).get("/health-test")

    assert response.status_code == 200
    assert response.json()["diagnostic"] == absolute
