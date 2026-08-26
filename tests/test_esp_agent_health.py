from __future__ import annotations

from aura_music_studio.esp_agent_health import creator_health, router


def test_health_queue_signals_are_explainable_and_non_punitive():
    health = creator_health({
        "training_average": 10,
        "progress_submissions": 0,
        "plan": {"completion_percent": 15},
        "open_followups": 3,
    })
    assert health["state"] == "needs_action"
    assert health["explainable"] is True
    assert health["automated_penalty"] is False
    assert health["source_scope"] == "assigned_creator_operational_data_only"
    assert any("Training" in reason for reason in health["reasons"])
    assert any("No LIVE/video" in reason for reason in health["reasons"])


def test_strong_creator_health_can_be_on_track():
    health = creator_health({
        "training_average": 90,
        "progress_submissions": 4,
        "plan": {"completion_percent": 100},
        "open_followups": 0,
    })
    assert health["state"] == "on_track"
    assert health["reasons"] == []
    assert len(health["positives"]) >= 3


def test_agent_health_router_exposes_private_queue_and_followup():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/health" in paths
    assert "/command-center/api/agent/health" in paths
    assert "/command-center/api/agent/health/follow-up" in paths
