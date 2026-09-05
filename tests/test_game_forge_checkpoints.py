from __future__ import annotations

import pytest

from aura_music_studio.game_forge_checkpoints import inject_checkpoint_controls


HASH_A = "a" * 64
HASH_B = "b" * 64


def _playtest_html() -> str:
    return """<!doctype html><html><head>
<meta http-equiv='Content-Security-Policy' content="default-src 'none'; connect-src 'none'">
</head><body><div id='media-controls'><button id='audio-toggle'>Audio</button></div>
<script>'use strict';const runtimeReady=true;</script></body></html>"""


def test_aura2d_save_system_is_hash_scoped_multislot_and_local_only() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_demo-1",
        content_hash=HASH_A,
    )

    assert "id='checkpoint-slot'" in rendered
    assert "value='slot1'" in rendered
    assert "value='slot2'" in rendered
    assert "value='slot3'" in rendered
    assert "id='checkpoint-name'" in rendered
    assert "id='checkpoint-save'" in rendered
    assert "id='checkpoint-load'" in rendered
    assert "id='checkpoint-continue'" in rendered
    assert "id='checkpoint-new'" in rendered
    assert "id='checkpoint-reset'" in rendered
    assert "aura.game-forge.save.v2:game_demo-1:" + HASH_A + ":" in rendered
    assert "aura.game-forge.checkpoint.v1:game_demo-1:" + HASH_A in rendered
    assert 'checkpointRuntime="aura2d"' in rendered
    assert "x:Number(p.x)" in rendered
    assert "score:Number(score)" in rendered
    assert "lives:Number(lives)" in rendered
    assert "localStorage.setItem" in rendered
    assert "localStorage.getItem" in rendered
    assert "localStorage.removeItem" in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert "WebSocket" not in rendered
    assert "connect-src 'none'" in rendered


def test_save_rows_are_versioned_named_and_bounded() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_named",
        content_hash=HASH_A,
    )

    assert "return {v:2,runtime:checkpointRuntime,slot,name:" in rendered
    assert ".trim().slice(0,40)" in rendered
    assert "row.v===2" in rendered
    assert "row.slot===slot" in rendered
    assert "row.name.length<=40" in rendered
    assert "score=Math.max(0,Math.trunc(s.score))" in rendered
    assert "lives=Math.max(1,Math.min(99,Math.trunc(s.lives)))" in rendered


def test_aura3d_save_system_captures_player_and_orbit_camera_state() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura3d",
        game_id="game_3d",
        content_hash=HASH_B,
    )

    assert 'checkpointRuntime="aura3d"' in rendered
    assert "x:Number(player.position.x)" in rendered
    assert "y:Number(player.position.y)" in rendered
    assert "z:Number(player.position.z)" in rendered
    assert "yaw:Number(yaw)" in rendered
    assert "pitch:Number(pitch)" in rendered
    assert "distance:Number(distance)" in rendered
    assert "pitch=Math.max(-.15,Math.min(1.2,s.pitch))" in rendered
    assert "distance=Math.max(4,Math.min(35,s.distance))" in rendered
    assert "worldLimit=1000000" in rendered


def test_continue_chooses_newest_save_including_autosave() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_continue",
        content_hash=HASH_A,
    )

    assert "checkpointAllSlots=[...checkpointSlots,'autosave']" in rendered
    assert "rows.sort((a,b)=>b.savedAt-a.savedAt)" in rendered
    assert "checkpointNewest()" in rendered
    assert "Continued from autosave." in rendered


def test_autosave_is_dirty_state_driven_and_pagehide_safe() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_autosave",
        content_hash=HASH_A,
    )

    assert "let checkpointDirty=false" in rendered
    assert "['keydown','pointerdown','wheel']" in rendered
    assert "setInterval(checkpointAutosave,20000)" in rendered
    assert "document.visibilityState==='hidden'" in rendered
    assert "addEventListener('pagehide'" in rendered
    assert "signature===checkpointLastAutosaveSignature" in rendered


def test_new_game_restores_initial_runtime_state_without_deleting_saves() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_new",
        content_hash=HASH_A,
    )

    assert "checkpointInitialState=checkpointCaptureState()" in rendered
    assert "checkpointApply(checkpointRow('autosave','New game',checkpointInitialState))" in rendered
    assert "Existing saves are preserved." in rendered


def test_v1_checkpoint_migrates_only_into_slot_one() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_migrate",
        content_hash=HASH_A,
    )

    assert "checkpointMigrateLegacy()" in rendered
    assert "row.v===1&&row.runtime===checkpointRuntime" in rendered
    assert "checkpointRow('slot1','Migrated checkpoint'" in rendered
    assert "localStorage.removeItem(checkpointLegacyKey)" in rendered
    assert "checkpointRow('slot2','Migrated checkpoint'" not in rendered


def test_save_identity_changes_with_build_hash() -> None:
    first = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_same", content_hash=HASH_A)
    second = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_same", content_hash=HASH_B)

    assert "game_same:" + HASH_A + ":" in first
    assert "game_same:" + HASH_B + ":" in second
    assert "game_same:" + HASH_B not in first


def test_save_injection_is_idempotent() -> None:
    once = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_demo", content_hash=HASH_A)
    twice = inject_checkpoint_controls(once, runtime="aura2d", game_id="game_demo", content_hash=HASH_A)

    assert twice == once
    assert twice.count("id='checkpoint-save'") == 1
    assert twice.count("id='checkpoint-slot'") == 1


@pytest.mark.parametrize(
    ("game_id", "content_hash"),
    [
        ("../game", HASH_A),
        ("game/child", HASH_A),
        ("game?query", HASH_A),
        ("game#fragment", HASH_A),
        ("game%2Fchild", HASH_A),
        ("game_ok", "A" * 64),
        ("game_ok", "abc"),
    ],
)
def test_unsafe_save_identity_is_rejected(game_id: str, content_hash: str) -> None:
    with pytest.raises(ValueError):
        inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id=game_id, content_hash=content_hash)


def test_missing_controls_or_script_fails_closed() -> None:
    with pytest.raises(ValueError, match="media-controls"):
        inject_checkpoint_controls("<html><script></script></html>", runtime="aura2d", game_id="game_demo", content_hash=HASH_A)

    with pytest.raises(ValueError, match="runtime script"):
        inject_checkpoint_controls("<div id='media-controls'></div>", runtime="aura2d", game_id="game_demo", content_hash=HASH_A)


def test_unknown_runtime_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported checkpoint runtime"):
        inject_checkpoint_controls(  # type: ignore[arg-type]
            _playtest_html(),
            runtime="unknown",
            game_id="game_demo",
            content_hash=HASH_A,
        )
