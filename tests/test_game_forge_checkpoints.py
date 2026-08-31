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


def test_aura2d_checkpoint_is_hash_scoped_and_local_only() -> None:
    rendered = inject_checkpoint_controls(
        _playtest_html(),
        runtime="aura2d",
        game_id="game_demo-1",
        content_hash=HASH_A,
    )

    assert "id='checkpoint-save'" in rendered
    assert "id='checkpoint-resume'" in rendered
    assert "id='checkpoint-reset'" in rendered
    assert "aura.game-forge.checkpoint.v1:game_demo-1:" + HASH_A in rendered
    assert 'checkpointRuntime="aura2d"' in rendered
    assert "x:p.x" in rendered
    assert "score:score" in rendered
    assert "lives:lives" in rendered
    assert "localStorage.setItem" in rendered
    assert "localStorage.getItem" in rendered
    assert "localStorage.removeItem" in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert "WebSocket" not in rendered
    assert "connect-src 'none'" in rendered


def test_aura3d_checkpoint_captures_player_and_camera_state() -> None:
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
    assert "yaw:yaw" in rendered
    assert "pitch:pitch" in rendered
    assert "distance:distance" in rendered
    assert "pitch=Math.max(-.15,Math.min(1.2,row.pitch))" in rendered
    assert "distance=Math.max(4,Math.min(35,row.distance))" in rendered


def test_checkpoint_identity_changes_with_build_hash() -> None:
    first = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_same", content_hash=HASH_A)
    second = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_same", content_hash=HASH_B)

    assert "game_same:" + HASH_A in first
    assert "game_same:" + HASH_B in second
    assert "game_same:" + HASH_B not in first


def test_checkpoint_injection_is_idempotent() -> None:
    once = inject_checkpoint_controls(_playtest_html(), runtime="aura2d", game_id="game_demo", content_hash=HASH_A)
    twice = inject_checkpoint_controls(once, runtime="aura2d", game_id="game_demo", content_hash=HASH_A)

    assert twice == once
    assert twice.count("id='checkpoint-save'") == 1


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
def test_unsafe_checkpoint_identity_is_rejected(game_id: str, content_hash: str) -> None:
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
