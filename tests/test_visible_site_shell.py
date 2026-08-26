from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.member_dashboard as dashboard
from aura_music_studio.brand_migration import rebrand_text


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
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def test_regular_member_dashboard_surfaces_aura_core_but_not_esp_social_tools(monkeypatch):
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
    assert "ESP areas use the same account and site" in text
    assert "access is owner-approved" in text
    assert "cannot be obtained merely by purchasing a creative subscription" in text
    assert "href='/social-house'" not in text
    assert "Enter ESP Hub" not in text


def test_approved_esp_member_gets_private_hub_entry_inside_same_site(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "creator"}).get("/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "Private Elevate Souls Productions Area" in text
    assert "additional areas inside this same Pulsar-Frequency House account" in text
    assert "These areas remain hidden from ordinary public members" in text
    assert "Enter ESP Hub" in text
    assert "href='/social-house'" not in text
