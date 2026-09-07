from __future__ import annotations

import re

import pytest

from aura_music_studio import game_forge_api
from aura_music_studio.game_forge_live_copilot import inject_live_copilot
from aura_music_studio.game_forge_live_voice import (
    MAX_VOICE_COMMAND_CHARS,
    VOICE_CHANNEL_QUERY,
    VOICE_MESSAGE_TYPE,
    VOICE_READY_MESSAGE_TYPE,
    install_live_voice_host_bridge,
    live_voice_host_fragment,
    new_voice_channel,
    private_voice_frame_url,
    voice_permissions_policy,
)
from aura_music_studio.game_forge_models import GameDNA


def _game() -> GameDNA:
    return GameDNA(
        id="game_voice_test",
        title="Voice Bridge Test",
        prompt="A private live creation voice bridge regression game",
        dimension="2d",
        engine_target="aura2d",
        rights_confirmed=True,
    )


def test_voice_channel_is_bounded_unpersisted_capability_shape():
    first = new_voice_channel()
    second = new_voice_channel()
    assert first != second
    assert 24 <= len(first) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert MAX_VOICE_COMMAND_CHARS == 240
    assert VOICE_MESSAGE_TYPE == "aura-live-command-v1"
    assert VOICE_READY_MESSAGE_TYPE == "aura-live-ready-v1"
    assert VOICE_CHANNEL_QUERY == "aura_live_channel"
    assert voice_permissions_policy() == "microphone=(self)"


def test_private_voice_frame_url_accepts_only_private_playtest_frame():
    channel = new_voice_channel()
    url = private_voice_frame_url(
        "/api/game-forge/games/game_voice_test/playtest-frame?existing=1",
        channel,
    )
    assert url.startswith("/api/game-forge/games/game_voice_test/playtest-frame?")
    assert "existing=1" in url
    assert f"{VOICE_CHANNEL_QUERY}={channel}" in url

    with pytest.raises(ValueError):
        private_voice_frame_url("/game-gallery/public/frame", channel)
    with pytest.raises(ValueError):
        private_voice_frame_url("https://example.invalid/frame", channel)
    with pytest.raises(ValueError):
        private_voice_frame_url("/api/game-forge/games/x/playtest-frame", "not valid!")


def test_private_host_gets_parent_voice_bridge_without_weakening_sandbox():
    # Importing the live copilot installs this idempotently on the existing Game Forge host helper.
    assert install_live_voice_host_bridge() is False
    response = game_forge_api._host_page(
        "Voice Game",
        "/api/game-forge/games/game_voice_test/playtest-frame",
        rating_line="Private test",
        popout=False,
        return_url="/game-creation?game=game_voice_test",
        popout_url="/game-creation/play/game_voice_test?popout=1",
    )
    html = response.body.decode("utf-8")

    assert response.headers["permissions-policy"] == "microphone=(self)"
    assert "id='aura-game-playtest-frame'" in html
    assert f"{VOICE_CHANNEL_QUERY}=" in html
    assert "id='aura-live-voice-controls'" in html
    assert "id='aura-live-voice-button' type='button' disabled" in html
    assert "Talk to Aura" in html
    assert "SpeechRecognition" in html
    assert "frame.contentWindow.postMessage" in html
    assert "type:messageType,game_id:gameId,channel,command" in html
    assert "event.source!==frame.contentWindow" in html
    assert "data.type!==readyType||data.game_id!==gameId||data.channel!==channel" in html
    assert "frameReady=true" in html
    assert "if(!frameReady)" in html
    assert "sandbox='allow-scripts allow-pointer-lock'" in html
    assert "allow-same-origin" not in html
    assert "allow='gamepad'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html


def test_public_gallery_host_does_not_receive_creator_voice_authority():
    response = game_forge_api._host_page(
        "Public Game",
        "/game-gallery/public-123/frame",
        rating_line="Public test",
        popout=False,
    )
    html = response.body.decode("utf-8")
    assert "aura-live-voice" not in html
    assert VOICE_CHANNEL_QUERY not in html
    assert "permissions-policy" not in response.headers
    assert "sandbox='allow-scripts allow-pointer-lock'" in html


def test_live_copilot_receiver_requires_parent_game_type_channel_and_command_bounds():
    game = _game()
    html = inject_live_copilot("<!doctype html><html><body></body></html>", game=game)

    assert f'"voice_message_type":"{VOICE_MESSAGE_TYPE}"' in html
    assert f'"voice_ready_message_type":"{VOICE_READY_MESSAGE_TYPE}"' in html
    assert f'"voice_channel_query":"{VOICE_CHANNEL_QUERY}"' in html
    assert "new URLSearchParams(location.search)" in html
    assert "event.source!==window.parent" in html
    assert "data.type!==liveCfg.voice_message_type" in html
    assert "data.game_id!==String(liveCfg.game_id)" in html
    assert "data.channel!==liveVoiceChannel" in html
    assert "typeof data.command!=='string'||data.command.length>liveMaxCommand" in html
    assert "type:liveCfg.voice_ready_message_type" in html
    assert "window.parent.postMessage" in html
    assert "liveMic.hidden=true" in html
    assert "liveCommand(command)" in html
    assert "project_persistence:false" in html
    assert "external_network_access:false" in html
    assert "arbitrary_code_execution:false" in html


def test_voice_host_fragment_rejects_invalid_channel_and_frame_id():
    channel = new_voice_channel()
    fragment = live_voice_host_fragment(game_id="game_voice_test", channel=channel)
    assert channel in fragment
    assert "Connecting Aura to the private playtest" in fragment
    assert "browser/vendor speech services" not in fragment  # detailed privacy note stays server-side only
    with pytest.raises(ValueError):
        live_voice_host_fragment(game_id="game_voice_test", channel="bad channel")
    with pytest.raises(ValueError):
        live_voice_host_fragment(game_id="game_voice_test", channel=channel, frame_id="1bad")
