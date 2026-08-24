from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from aura_music_studio import aura_workspace_theme_tools
from aura_music_studio.owner_identity import ADMIN_COOKIE, OWNER_ACTOR_COOKIE, issue_actor_token
from aura_music_studio.owner_workspace_theme_bridge import OwnerWorkspaceThemeBridgeMiddleware
from aura_music_studio.request_context import (
    current_owner_actor_id,
    current_owner_actor_name,
    reset_current_owner_actor,
    set_current_owner_actor,
)
from aura_music_studio.workspace_theme import WorkspaceThemeStore


class _Member:
    user_id = "member-1"


def test_owner_context_is_request_local_and_resettable():
    assert current_owner_actor_id() is None
    tokens = set_current_owner_actor("kev", "Kev")
    try:
        assert current_owner_actor_id() == "kev"
        assert current_owner_actor_name() == "Kev"
        assert aura_workspace_theme_tools._subject(_Member()) == "owner:kev"
        assert "Kev" in aura_workspace_theme_tools._actor_label(_Member())
    finally:
        reset_current_owner_actor(tokens)
    assert current_owner_actor_id() is None
    assert current_owner_actor_name() is None
    assert aura_workspace_theme_tools._subject(_Member()) == "member:member-1"


def test_selected_owner_theme_follows_owner_into_non_owner_creative_page(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-secret-for-test")
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    preview = store.create_preview("owner:kev", {"accent": "#ff8800"}, "Kev prefers orange")
    store.confirm("owner:kev", preview["preview_id"], "Kev — ESP Co-Owner")

    import aura_music_studio.owner_workspace_theme_bridge as bridge

    monkeypatch.setattr(bridge, "themes", store)
    app = FastAPI()
    app.add_middleware(OwnerWorkspaceThemeBridgeMiddleware)

    @app.get("/aura", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><head></head><body><h1>Aura</h1></body></html>")

    client = TestClient(app)
    token = issue_actor_token("kev")
    response = client.get(
        "/aura",
        cookies={ADMIN_COOKIE: "owner-secret-for-test", OWNER_ACTOR_COOKIE: token},
    )
    assert response.status_code == 200
    assert "--workspace-accent:#ff8800" in response.text
    assert "esp-owner-workspace-actor" in response.text
    assert "content='kev'" in response.text


def test_owner_actor_cookie_alone_does_not_apply_owner_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-secret-for-test")
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    preview = store.create_preview("owner:mary", {"accent": "#ff55cc"}, "Mary theme")
    store.confirm("owner:mary", preview["preview_id"], "Mary — ESP Co-Owner")

    import aura_music_studio.owner_workspace_theme_bridge as bridge

    monkeypatch.setattr(bridge, "themes", store)
    app = FastAPI()
    app.add_middleware(OwnerWorkspaceThemeBridgeMiddleware)

    @app.get("/aura", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><head></head><body>Aura</body></html>")

    client = TestClient(app)
    token = issue_actor_token("mary")
    response = client.get("/aura", cookies={OWNER_ACTOR_COOKIE: token})
    assert response.status_code == 200
    assert "--workspace-accent:#ff55cc" not in response.text
    assert "esp-owner-workspace-actor" not in response.text
