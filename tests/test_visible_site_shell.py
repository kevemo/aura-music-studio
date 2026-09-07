from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.member_dashboard as dashboard
from aura_music_studio.brand_migration import rebrand_text
from aura_music_studio.native_access import EffectiveNativeAccess


def test_public_home_presentation_migration_is_current_truthful_and_idempotent():
    source = """
    <a href='#suite'>Creative House</a>
    <span>Workspace architecture staged</span>
    <span>Aura routes connected</span>
    <span>Unified project layer in build</span>
    <h3>Base</h3><a href='/signup?plan=base'>Choose Base</a>
    <p>The target project model keeps music sections, stems, scenes, visual layers, voice assets and generation settings addressable</p>
    <p>The platform is being built around continuity:</p>
    <p>The current real-audio music engine, owner controls and ESP permission systems remain underneath the new master brand while the unified video, image and multimodal editing layers are expanded.</p>
    <section class='wrap section'><div class='eyebrow'>Memberships</div>
    """
    current = rebrand_text(source)
    assert "href='/creative-house'>Creative House" in current
    assert "Creative DNA + renderer bridge connected" in current
    assert "Aura Core 0.20 connected" in current
    assert "Creative DNA project layer connected" in current
    assert "<h3>Basic</h3>" in current
    assert "href='/signup?plan=base'>Choose Basic" in current
    assert "Creative DNA project model keeps" in current
    assert "built around Creative DNA continuity" in current
    assert "external generation backends remain deployment-configurable" in current
    assert "data-pfh-aura-core='0.20'" in current
    assert "Host runtime connected · final rig pending" in current
    assert "External AI models, speech services, renderers, OAuth services and the final 3D rig have separate runtime/configuration states" in current
    assert "Workspace architecture staged" not in current
    assert "Unified project layer in build" not in current

    again = rebrand_text(current)
    assert again.count("data-pfh-aura-core='0.20'") == 1


def _client(monkeypatch, membership):
    user = {
        "id": "member-visible-shell",
        "display_name": "Studio Member",
        "status": "active",
        "plan_id": "free",
        "requested_plan_id": "free",
    }
    monkeypatch.setattr(dashboard.accounts, "resolve_session", lambda _cookie: user)
    monkeypatch.setattr(dashboard.esp, "membership", lambda _user_id: membership)
    monkeypatch.setattr(
        dashboard.native_access,
        "resolve",
        lambda user_id: EffectiveNativeAccess(
            user_id=user_id,
            membership_plan_id="free",
            membership_entitlements=frozenset(),
            purchased_entitlements=frozenset(),
        ),
    )
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def test_regular_member_dashboard_surfaces_aura_core_game_forge_marketplace_and_truthful_aura_sec_product(monkeypatch):
    response = _client(monkeypatch, None).get("/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "Aura Core 0.20" in text
    assert "Aura Today" in text
    assert "Voice Conversation" in text
    assert "Artifacts" in text
    assert "Tasks &amp; Briefings" in text
    assert "Connected Workspace" in text
    assert "Verified Workflows" in text
    assert "Open Aura Intelligence" in text
    assert "Pulsar-Frequency House is one integrated creation platform" in text

    assert "Game Forge" in text
    assert "Open Game Forge" in text
    assert "href='/game-creation'" in text

    assert "Marketplace Account" in text
    assert "Open Marketplace Account" in text
    assert "href='/marketplace/account'" in text
    assert "marketplace participation remains opt-in" in text

    assert "Aura Sec Security Center" in text
    assert "Aura Sec available · same account" in text
    assert "included with Unlimited Pro" in text
    assert "can also be purchased separately where offered" in text
    assert "Commercial access never grants native device trust by itself" in text
    assert "The browser is a member-safe control plane only" in text
    assert "href='/aura-sec'" in text
    assert "href='/account/native-products'" in text
    assert text.count("<article class='tool'>") == 8
    assert "<section class='security'>" in text

    assert "ESP areas use the same account and site" in text
    assert "access is owner-approved" in text
    assert "cannot be obtained merely by purchasing a creative subscription" in text
    assert "href='/social-house'" not in text
    assert "Enter ESP Hub" not in text


def test_dashboard_aura_sec_entry_never_exposes_native_authority_links(monkeypatch):
    response = _client(monkeypatch, None).get("/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "href='/aura-sec'" in text
    assert "href='/aura-sec/native/" not in text
    assert "href='/aura-sec/sign'" not in text
    assert "href='/aura-sec/approve'" not in text
    assert "href='/aura-sec/actions/execute'" not in text
    assert "cannot execute endpoint commands" in text
    assert "cannot" in text and "access command-signing keys" in text


def test_approved_esp_member_gets_private_hub_and_truthful_aura_sec_entry(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "creator"}).get("/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "Aura Sec Security Center" in text
    assert "Aura Sec available · same account" in text
    assert "href='/aura-sec'" in text
    assert "Private Elevate Souls Productions Area" in text
    assert "additional areas inside this same Pulsar-Frequency House account" in text
    assert "These areas remain hidden from ordinary public members" in text
    assert "Enter ESP Hub" in text
    assert "href='/social-house'" not in text
