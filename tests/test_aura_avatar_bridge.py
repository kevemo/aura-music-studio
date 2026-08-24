from pathlib import Path

import pytest

from aura_music_studio.aura_avatar_bridge import AuraAvatarBridge, AuraAvatarBridgeError


def test_page_context_only_accepts_runtime_control_selectors(tmp_path: Path):
    bridge = AuraAvatarBridge(tmp_path / "aura.sqlite3")
    result = bridge.register_page(
        "user-a",
        path="/studio",
        title="Studio",
        controls=[
            {"id": "safe-1", "label": "Generate Song", "selector": '[data-aura-control-id="safe-1"]', "kind": "button"},
            {"id": "bad-1", "label": "Unsafe", "selector": "body script", "kind": "button"},
        ],
    )
    assert [item["id"] for item in result["controls"]] == ["safe-1"]


def test_guide_to_requires_registered_current_control(tmp_path: Path):
    bridge = AuraAvatarBridge(tmp_path / "aura.sqlite3")
    bridge.register_page(
        "user-a",
        path="/studio",
        title="Studio",
        controls=[{"id": "video", "label": "Video Studio", "selector": '[data-aura-control-id="video"]', "kind": "a"}],
    )
    command = bridge.enqueue(
        "user-a",
        action="guide_to",
        control_id="video",
        message="This opens the Video Studio.",
        speak=True,
    )
    assert command["queued"] is True
    assert command["target_label"] == "Video Studio"

    with pytest.raises(AuraAvatarBridgeError, match="no longer available"):
        bridge.enqueue("user-a", action="guide_to", control_id="invented", message="no", speak=False)


def test_avatar_commands_are_user_isolated_and_consumed_once(tmp_path: Path):
    bridge = AuraAvatarBridge(tmp_path / "aura.sqlite3")
    bridge.enqueue("user-a", action="celebrate", message="Done", speak=True)
    bridge.enqueue("user-b", action="think", message="Working", speak=False)

    first_a = bridge.consume_next("user-a")
    assert first_a is not None
    assert first_a["action"] == "celebrate"
    assert first_a["payload"]["message"] == "Done"
    assert bridge.consume_next("user-a") is None

    first_b = bridge.consume_next("user-b")
    assert first_b is not None
    assert first_b["action"] == "think"
    assert bridge.consume_next("user-b") is None


def test_non_target_actions_do_not_require_page_context(tmp_path: Path):
    bridge = AuraAvatarBridge(tmp_path / "aura.sqlite3")
    for action in ("present", "celebrate", "minimize", "restore", "listen", "think"):
        result = bridge.enqueue("user-a", action=action, message="", speak=False)
        assert result["queued"] is True
        assert result["action"] == action
