from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.web_access import AuraWebGateway


def test_member_project_roots_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())

    token_a = set_current_user_id("member-a")
    try:
        root_a = tenant_storage.projects_root()
        (root_a / "song").mkdir(parents=True)
        assert tenant_storage.project_path("song") == (root_a / "song").resolve()
    finally:
        reset_current_user_id(token_a)

    token_b = set_current_user_id("member-b")
    try:
        root_b = tenant_storage.projects_root()
        assert root_b != root_a
        assert not (root_b / "song").exists()
        with pytest.raises(FileNotFoundError):
            tenant_storage.project_path("song")
    finally:
        reset_current_user_id(token_b)


def test_project_name_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    token = set_current_user_id("member-a")
    try:
        for bad in ("../other", "a/b", "..", "/absolute", "x\\y"):
            with pytest.raises(ValueError):
                tenant_storage.safe_project_name(bad)
    finally:
        reset_current_user_id(token)


def test_web_gateway_blocks_obvious_private_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.delenv("AURA_WEB_ALLOWED_DOMAINS", raising=False)
    gateway = AuraWebGateway(cache_dir=tmp_path)
    for url in (
        "https://localhost/private",
        "https://127.0.0.1/private",
        "https://0.0.0.0/private",
    ):
        with pytest.raises(PermissionError):
            gateway._validate_url(url)


def test_web_gateway_requires_https_for_public_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("AURA_WEB_ALLOW_HTTP", "false")
    gateway = AuraWebGateway(cache_dir=tmp_path)
    with pytest.raises(ValueError):
        gateway._validate_url("http://example.com")


def test_internal_searxng_is_special_case_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("AURA_WEB_ALLOW_HTTP", "false")
    monkeypatch.setenv("AURA_SEARXNG_URL", "http://searxng:8080")
    gateway = AuraWebGateway(cache_dir=tmp_path)
    # The private Compose search service is permitted only when explicitly treated as the
    # configured search backend.
    gateway._validate_url("http://searxng:8080/search", allow_configured_search=True)
    with pytest.raises(ValueError):
        gateway._validate_url("http://searxng:8080/search")
