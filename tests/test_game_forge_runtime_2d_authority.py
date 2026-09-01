from __future__ import annotations

import aura_music_studio.game_forge_runtime_2d as runtime_2d


def test_authored_gameplay_replaces_native_demo_scaffold() -> None:
    script = runtime_2d._bridge_script()

    assert "const authoredGameplay=entities.some" in script
    assert "stars.length=0" in script
    assert "haz.length=0" in script


def test_initial_world_state_is_applied_after_2d_entities_exist() -> None:
    script = runtime_2d._bridge_script()

    assert "if(typeof auraApplyAll==='function')auraApplyAll();" in script
    assert script.index("auraApplyAll();") < script.index("syncFromState();\nlet last=performance.now()")
