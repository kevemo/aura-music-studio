from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_community import _safe_chat, _safe_reactions
from aura_music_studio.creation_live_ui_community import harden_community_ui
from aura_music_studio.route_integrity import deduplicate_http_routes, duplicate_http_signatures
from aura_music_studio.shared_sky_live_community import community


def test_chat4_merged_contract_is_consumed_not_reimplemented():
    assert callable(community.detail)
    assert callable(community.chat_history)
    assert callable(community.presence_count)
    assert not hasattr(cl.creation_live_store, "send_chat")
    assert not hasattr(cl.creation_live_store, "debit_wallet")
    assert not hasattr(cl.creation_live_store, "score_battle")


def test_creation_community_route_replaces_compatibility_handler_once():
    app = FastAPI()
    deduplicate_http_routes(app)
    deduplicate_http_routes(app)

    routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/creation-live/projects/{project_name}/community"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "aura_music_studio.creation_live_community"
    assert duplicate_http_signatures(app.router.routes) == {}


def test_chat_projection_keeps_display_fields_and_drops_unknown_internal_fields():
    rows = _safe_chat(
        [
            {
                "id": "m1",
                "sender_user_id": "viewer-1",
                "body": "Make the logo bigger",
                "created_at": "2026-09-05T00:00:00+00:00",
                "deleted": False,
                "provider_payload": {"token": "must-not-leak"},
                "ip_hash": "private-internal-field",
            }
        ]
    )
    assert rows[0]["body"] == "Make the logo bigger"
    assert "provider_payload" not in rows[0]
    assert "ip_hash" not in rows[0]


def test_reaction_projection_is_numeric_and_non_negative():
    assert _safe_reactions({"heart": 4, "wow": "2", "bad": -3, "junk": "x"}) == {
        "heart": 4,
        "wow": 2,
    }


def test_community_ui_is_read_only_and_uses_text_nodes_for_chat():
    script = harden_community_ui(cl.LIVE_UI_SCRIPT)
    assert "Shared Sky community" in script
    assert "Community is display-only here" in script
    assert "body.textContent" in script
    assert "/creation-live/projects/${encodeURIComponent(pid)}/community" in script
    assert "project_mutated" not in script
    assert "debit" not in script.lower()
    assert "battle score" not in script.lower()
