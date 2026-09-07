from __future__ import annotations

from aura_music_studio.game_forge_checkpoints import inject_checkpoint_controls


HASH = "c" * 64


def _html() -> str:
    return """<!doctype html><html><body><div id='media-controls'></div><script>
'use strict';const p={x:1,y:2,r:1};let W=100,H=100,score=0,lives=3;
let player={position:{x:1,y:2,z:3}};let yaw=0,pitch=0,distance=12;
window.AuraRuntimeState={exportState:()=>({v:1}),validateState:s=>s?.v===1,importState:_s=>true};
</script></body></html>"""


def test_aura2d_save_rows_embed_validated_runtime_state() -> None:
    rendered = inject_checkpoint_controls(_html(), runtime="aura2d", game_id="game_state", content_hash=HASH)

    assert "aura:window.AuraRuntimeState?.exportState?.()||null" in rendered
    assert "window.AuraRuntimeState?.validateState?.(s.aura)" in rendered
    assert "window.AuraRuntimeState?.importState" in rendered
    assert "window.AuraMarkSaveDirty=()=>{checkpointDirty=true}" in rendered
    assert "aura:null" in rendered


def test_aura3d_save_rows_embed_validated_runtime_state() -> None:
    rendered = inject_checkpoint_controls(_html(), runtime="aura3d", game_id="game_state_3d", content_hash=HASH)

    assert "distance:Number(distance),aura:window.AuraRuntimeState?.exportState?.()||null" in rendered
    assert "window.AuraRuntimeState?.validateState?.(s.aura)" in rendered
    assert "window.AuraRuntimeState.importState(s.aura)" in rendered


def test_existing_version_two_rows_without_runtime_snapshot_remain_compatible() -> None:
    rendered = inject_checkpoint_controls(_html(), runtime="aura2d", game_id="game_compat", content_hash=HASH)

    assert "s.aura===null||s.aura===undefined" in rendered
    assert "row.v===2" in rendered
