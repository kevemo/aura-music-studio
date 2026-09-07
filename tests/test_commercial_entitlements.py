from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.commercial_entitlements import (
    can_download_media,
    image_poster_usage,
    record_image_poster_generation,
    require_image_poster_generation,
    require_media_download,
)
from aura_music_studio.creative_media_preview import router as base_media_router
from aura_music_studio.creative_project_api import router as base_creative_router
from aura_music_studio.creative_version_autopromotion import router as entitlement_overlay_router
from aura_music_studio.plans import get_plan, public_plans


def active_member(tmp_path: Path, monkeypatch, plan_id: str):
    db_path = tmp_path / f"{plan_id}.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db_path))
    store = AccountStore(db_path)
    signup = store.signup(
        f"{plan_id}@example.test",
        f"{plan_id.title()} Tester",
        "commercial-test-password",
        plan_id,
    )
    user = store.decide_membership(signup.approval_token, "approve", "Test Owner")
    if plan_id != "free":
        user = store.activate_paid_plan(user["id"], plan_id, "test-payment")
    return SimpleNamespace(user_id=user["id"], plan=get_plan(user["plan_id"]))


def test_public_plans_expose_authoritative_image_poster_limits():
    plans = {row["id"]: row for row in public_plans()}
    assert plans["free"]["image_poster_creations_per_day"] == 5
    assert plans["free"]["image_poster_creations_unlimited"] is False
    assert plans["base"]["image_poster_creations_per_day"] == 10
    assert plans["base"]["image_poster_creations_unlimited"] is False
    assert plans["pro"]["image_poster_creations_per_day"] is None
    assert plans["pro"]["image_poster_creations_unlimited"] is True


def test_free_image_quota_is_five_and_music_video_downloads_are_blocked(tmp_path, monkeypatch):
    member = active_member(tmp_path, monkeypatch, "free")
    assert can_download_media(member, "image") is True
    assert can_download_media(member, "music") is False
    assert can_download_media(member, "audio") is False
    assert can_download_media(member, "video") is False

    for index in range(5):
        require_image_poster_generation(member)
        status = record_image_poster_generation(
            member,
            project_id="free-project",
            directive_id=f"image-{index}",
        )
    assert status["used"] == 5
    assert status["remaining"] == 0
    with pytest.raises(PermissionError, match="5 per day"):
        require_image_poster_generation(member)
    with pytest.raises(PermissionError, match="£5.99 Tier 2"):
        require_media_download(member, "music")
    with pytest.raises(PermissionError, match="£5.99 Tier 2"):
        require_media_download(member, "video")


def test_basic_image_quota_is_ten_and_music_video_downloads_are_enabled(tmp_path, monkeypatch):
    member = active_member(tmp_path, monkeypatch, "base")
    assert can_download_media(member, "image") is True
    assert can_download_media(member, "music") is True
    assert can_download_media(member, "video") is True

    for index in range(10):
        require_image_poster_generation(member)
        status = record_image_poster_generation(
            member,
            project_id="basic-project",
            directive_id=f"image-{index}",
        )
    assert status["used"] == 10
    assert status["remaining"] == 0
    with pytest.raises(PermissionError, match="10 per day"):
        require_image_poster_generation(member)


def test_pro_image_creation_is_unlimited_and_downloads_are_enabled(tmp_path, monkeypatch):
    member = active_member(tmp_path, monkeypatch, "pro")
    for index in range(25):
        require_image_poster_generation(member)
        status = record_image_poster_generation(
            member,
            project_id="pro-project",
            directive_id=f"image-{index}",
        )
    assert status["used"] == 25
    assert status["limit"] is None
    assert status["remaining"] is None
    assert status["unlimited"] is True
    assert can_download_media(member, "image") is True
    assert can_download_media(member, "music") is True
    assert can_download_media(member, "video") is True


def test_entitlement_overlay_precedes_unrestricted_base_routes():
    """Validate route matching plus source order without FastAPI private-router introspection.

    FastAPI 0.141 keeps nested routers behind internal included-router objects, so inspecting
    ``app.routes`` no longer proves nested route precedence. Request-level matching plus the
    explicit production include order is stable across that implementation detail.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(entitlement_overlay_router)
    app.include_router(base_creative_router)
    app.include_router(base_media_router)
    client = TestClient(app, raise_server_exceptions=False)

    render = client.post(
        "/creative/projects/example/directives/example/render",
        json={"renderer": "image"},
    )
    media = client.get(
        "/creative/projects/example/elements/example/media?download=true"
    )
    assert render.status_code != 404
    assert media.status_code != 404

    app_source = Path("app.py").read_text(encoding="utf-8")
    assert app_source.index("app.include_router(commercial_entitlement_router)") < app_source.index(
        "app.include_router(creative_project_router)"
    )
    assert app_source.index("app.include_router(commercial_entitlement_router)") < app_source.index(
        "app.include_router(creative_media_preview_router)"
    )
    overlay_source = Path("aura_music_studio/creative_version_autopromotion.py").read_text(encoding="utf-8")
    assert "router.include_router(commercial_entitlement_router)" in overlay_source


def test_usage_status_starts_at_zero(tmp_path, monkeypatch):
    member = active_member(tmp_path, monkeypatch, "free")
    status = image_poster_usage(member)
    assert status == {
        "plan": "free",
        "limit": 5,
        "used": 0,
        "remaining": 5,
        "unlimited": False,
        "timezone": "UTC",
    }
