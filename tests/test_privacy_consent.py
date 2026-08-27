from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.privacy_consent import ConsentStore, evaluate_consent, router


def test_unknown_and_uk_profiles_fail_closed_for_nonessential_categories():
    unknown = evaluate_consent(profile="unknown", preferences={})
    uk = evaluate_consent(profile="uk", preferences={})
    for result in (unknown, uk):
        assert result["categories"] == {
            "necessary": True,
            "preferences": False,
            "analytics": False,
            "marketing": False,
        }
        assert result["jurisdiction_verified"] is False
        assert result["grants_esp_role_or_permission"] is False


def test_affirmative_choices_enable_only_selected_nonessential_categories():
    result = evaluate_consent(
        profile="uk",
        preferences={"preferences": True, "analytics": True, "marketing": False},
    )
    assert result["categories"]["necessary"] is True
    assert result["categories"]["preferences"] is True
    assert result["categories"]["analytics"] is True
    assert result["categories"]["marketing"] is False


def test_gpc_forces_marketing_off_and_never_enables_other_categories():
    result = evaluate_consent(
        profile="california",
        preferences={"preferences": True, "analytics": False, "marketing": True},
        gpc=True,
    )
    assert result["gpc_observed"] is True
    assert result["categories"]["marketing"] is False
    assert result["categories"]["preferences"] is True
    assert result["categories"]["analytics"] is False


def test_member_evidence_is_idempotent_and_scoped(tmp_path):
    store = ConsentStore(tmp_path / "privacy.sqlite3")
    first = store.record(
        user_id="member-a", profile="uk",
        preferences={"necessary": True, "preferences": False, "analytics": True, "marketing": False},
        gpc=False,
    )
    duplicate = store.record(
        user_id="member-a", profile="uk",
        preferences={"necessary": True, "preferences": False, "analytics": True, "marketing": False},
        gpc=False,
    )
    changed = store.record(
        user_id="member-a", profile="uk",
        preferences={"necessary": True, "preferences": False, "analytics": False, "marketing": False},
        gpc=False,
    )
    store.record(
        user_id="member-b", profile="uk",
        preferences={"necessary": True, "preferences": False, "analytics": False, "marketing": False},
        gpc=False,
    )
    assert duplicate["id"] == first["id"]
    assert changed["id"] != first["id"]
    assert store.latest("member-a")["id"] == changed["id"]
    assert store.latest("member-b")["user_id"] == "member-b"


def test_public_state_reads_gpc_without_persisting_or_claiming_jurisdiction(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/privacy/consent?profile=california", headers={"Sec-GPC": "1"})
    assert response.status_code == 200
    body = response.json()
    assert body["gpc_observed"] is True
    assert body["categories"]["marketing"] is False
    assert body["jurisdiction_verified"] is False
    assert body["authenticated_preference_evidence"] is False


def test_persistence_requires_authenticated_member_and_ignores_spoofed_identity(monkeypatch, tmp_path):
    db = tmp_path / "privacy.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    unauth = FastAPI()
    unauth.include_router(router)
    assert TestClient(unauth).post(
        "/privacy/consent",
        json={"profile": "uk", "analytics": True, "user_id": "attacker"},
    ).status_code == 401

    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="authenticated-member")
        return await call_next(request)

    app.include_router(router)
    response = TestClient(app).post(
        "/privacy/consent",
        json={"profile": "uk", "analytics": True, "marketing": True, "user_id": "attacker"},
        headers={"Sec-GPC": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["categories"]["analytics"] is True
    assert body["categories"]["marketing"] is False
    assert body["automatic_tracker_execution"] is False
    assert ConsentStore(db).latest("authenticated-member") is not None
    assert ConsentStore(db).latest("attacker") is None


def test_integration_overlay_mounts_consent_routes():
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    paths = app.openapi()["paths"]
    assert "/privacy/consent" in paths
