from __future__ import annotations

import aura_music_studio.game_forge_runtime_2d as runtime_2d


def test_world_spawn_drives_native_canvas_player_without_duplicate_actor() -> None:
    script = runtime_2d._bridge_script()

    assert "spawnEntity=entities.find(row=>row.kind==='spawn')" in script
    assert "playerEntity=entities.find(row=>row.kind==='player')" in script
    assert "authoredStart=spawnEntity?.position||playerEntity?.position||null" in script
    assert "p.x=Math.max(p.r,Math.min(W-p.r,start.x))" in script
    assert "p.y=Math.max(topInset+p.r,Math.min(H-p.r,start.y))" in script
    assert "if(row.kind==='player'||row.kind==='spawn'||!entityEnabled(row)" in script


def test_bridge_contract_declares_world_spawn_binding() -> None:
    source = runtime_2d.aura2d_world_bridge_payload
    assert source is not None
    script = runtime_2d._bridge_script()
    assert "projection:'world_xz_to_canvas_v1'" in script
