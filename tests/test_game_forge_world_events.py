from __future__ import annotations

import hashlib
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import aura_agent_tools as aura_tools
from aura_music_studio import game_forge_store as store
from aura_music_studio.aura_world_events_tools import install_aura_world_events_tools
from aura_music_studio.game_forge_assets import GameAssetManifest, GameAssetRecord, save_asset_manifest
from aura_music_studio.game_forge_integrity import assess_game_integrity, game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_world import BehaviorNodeDNA, Vec3, WorldEntityDNA, ensure_world, load_world, save_world
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.game_forge_world_events import (
    CreateWorldEventEntityRequest,
    create_world_event_entity,
    world_event_publication_blockers,
    world_event_runtime_payload,
)
from aura_music_studio.game_forge_world_events_runtime import build_world_events_playtest, render_world_events_playtest
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="world-events-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _game(monkeypatch, tmp_path, *, dimension="2d"):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="World Events DNA",
        prompt="A private declarative world used to verify safe traversal, atmosphere and event runtime behavior",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
        rights_attestation="Test fixture rights confirmed",
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def _audio_asset(game: GameDNA) -> GameAssetRecord:
    media = b"RIFF"
    record = GameAssetRecord(
        id="asset_audio_fixture",
        game_id=game.id,
        kind="audio",
        label="Forest Ambience",
        role="ambient world audio",
        source_project="test_project",
        source_element_id="audio_1",
        source_media_sha256=hashlib.sha256(media).hexdigest(),
        imported_filename="asset_audio_fixture.wav",
        byte_size=len(media),
        rights_confirmed=True,
        rights_attestation="Test fixture rights confirmed",
    )
    save_asset_manifest(GameAssetManifest(game_id=game.id, assets=[record]))
    asset_dir = store.game_dir(game.id) / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / record.imported_filename).write_bytes(media)
    return record


def test_no_world_events_preserve_previous_cumulative_runtime(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, html = build_world_events_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v3_adventure"
    assert "aura-world-events-dna" not in html
    assert game.latest_build.content_hash == game_integrity_hash(game)


def test_world_event_presets_invalidate_stale_build_and_are_integrity_bound(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, _ = build_world_events_playtest(game)
    before = game_integrity_hash(game)
    assert game.latest_build is not None

    portal = create_world_event_entity(
        game,
        CreateWorldEventEntityRequest(
            name="North Portal",
            preset="spawn_portal",
            position=Vec3(x=5, y=0, z=0),
            target_spawn_id="spawn",
        ),
    )
    assert portal.behaviors[0].op == "spawn"
    assert portal.behaviors[0].params["target_spawn_id"] == "spawn"
    assert game.latest_build is None
    assert game.rating_assessment is None
    assert game.status == "draft"
    assert game_integrity_hash(game) != before


def test_world_event_runtime_payload_strips_scripts_urls_and_clamps_values(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    asset = _audio_asset(game)
    world = load_world(game.id)
    audio = WorldEntityDNA(
        name="Unsafe-looking Audio Zone",
        kind="audio",
        visible=False,
        behaviors=[
            BehaviorNodeDNA(
                op="audio_zone",
                params={
                    "asset_id": asset.id,
                    "radius": 999999999,
                    "volume": 9,
                    "loop": "yes",
                    "fade_seconds": -4,
                    "url": "https://evil.example/audio.mp3",
                    "script": "fetch('https://evil.example')",
                },
            )
        ],
    )
    particles = WorldEntityDNA(
        name="Extreme Particles",
        kind="vfx",
        visible=False,
        behaviors=[
            BehaviorNodeDNA(
                op="particle_emitter",
                params={"rate": 9999, "lifetime_seconds": 999, "speed": 999, "size": 999, "max_particles": 99999, "color": "javascript:bad"},
            )
        ],
    )
    world.entities.extend([audio, particles])
    world.touch()
    save_world(world)

    payload = world_event_runtime_payload(game.id)
    audio_params = next(row for row in payload["entities"] if row["id"] == audio.id)["behaviors"][0]["params"]
    particle_params = next(row for row in payload["entities"] if row["id"] == particles.id)["behaviors"][0]["params"]
    assert audio_params == {
        "asset_id": asset.id,
        "media_url": "media/asset_audio_fixture.wav",
        "radius": 100000.0,
        "volume": 1.0,
        "loop": True,
        "fade_seconds": 0.05,
    }
    assert "url" not in audio_params and "script" not in audio_params
    assert particle_params["rate"] == 240.0
    assert particle_params["lifetime_seconds"] == 30.0
    assert particle_params["speed"] == 100.0
    assert particle_params["size"] == 64.0
    assert particle_params["max_particles"] == 512
    assert particle_params["color"] == "#5be1ff"
    assert payload["verified_same_origin_media_only"] is True
    assert payload["external_media_urls_allowed"] is False
    assert payload["arbitrary_script_source_allowed"] is False


def test_world_event_publication_blockers_fail_closed(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    world = load_world(game.id)
    bad_portal = WorldEntityDNA(
        name="Broken Portal",
        kind="trigger",
        physics=None,
        behaviors=[BehaviorNodeDNA(op="spawn", params={"target_spawn_id": "missing_spawn"})],
    )
    bad_audio = WorldEntityDNA(
        name="Broken Audio",
        kind="audio",
        visible=False,
        behaviors=[BehaviorNodeDNA(op="audio_zone", params={"asset_id": "missing_asset"})],
    )
    world.entities.extend([bad_portal, bad_audio])
    world.touch()
    save_world(world)

    blockers = world_event_publication_blockers(game.id)
    text = " ".join(blockers).lower()
    assert "broken portal" in text and "missing" in text and "trigger physics" in text
    assert "broken audio" in text and "missing game asset" in text
    assessment = assess_game_integrity(game)
    assert assessment.public_test_allowed is False
    assert any("Broken Portal" in blocker for blocker in assessment.blockers)


def test_world_events_2d_runtime_layers_on_adventure_and_stays_off_network(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    create_world_event_entity(
        game,
        CreateWorldEventEntityRequest(name="Fireflies", preset="particle_emitter", position=Vec3(x=2, y=1, z=0), particle_color="#79dda5"),
    )
    game, html = build_world_events_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v5_world_events"
    assert "id='aura-world-events-dna'" in html
    assert "id='aura-adventure-dna'" in html
    assert "auraGameplayUpdate" in html
    assert "eventParticles" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert game.latest_build.content_hash == game_integrity_hash(game)


def test_world_events_3d_runtime_supports_portals_audio_and_particles(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    asset = _audio_asset(game)
    create_world_event_entity(game, CreateWorldEventEntityRequest(name="Portal", preset="spawn_portal", position=Vec3(x=2, y=1, z=0)))
    create_world_event_entity(game, CreateWorldEventEntityRequest(name="Forest Zone", preset="audio_zone", position=Vec3(x=3, y=1, z=0), audio_asset_id=asset.id, radius=8, volume=.5))
    create_world_event_entity(game, CreateWorldEventEntityRequest(name="Magic Dust", preset="particle_emitter", position=Vec3(x=4, y=1, z=0)))
    game, html = build_world_events_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v8_world_events"
    assert "gl.drawArrays" in html
    assert "aura-world-events-dna" in html
    assert "world-audio-toggle" in html
    assert "Enable World Audio" in html
    assert "media/asset_audio_fixture.wav" in html
    assert "external_media_urls_allowed" in html


def test_world_event_routes_workspace_and_authoritative_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)

    created = client.post(
        f"/api/game-forge/games/{game.id}/world-events/entities",
        json={
            "name": "API Particles",
            "preset": "particle_emitter",
            "position": {"x": 1, "y": 2, "z": 0},
            "scale": {"x": 1, "y": 1, "z": 1},
            "particle_color": "#efca6d",
        },
    )
    assert created.status_code == 200
    event_id = created.json()["entity"]["id"]

    state = client.get(f"/api/game-forge/games/{game.id}/world-events")
    assert state.status_code == 200
    assert any(row["id"] == event_id for row in state.json()["entities"])

    editor = client.get(f"/game-creation/world-events/{game.id}")
    assert editor.status_code == 200
    assert "World Events &amp; Atmosphere" in editor.text or "World Events & Atmosphere" in editor.text
    assert "World Audio" in editor.text
    assert "/world-events/entities" in editor.text
    assert "arbitrary scripts" in editor.text.lower()

    built = client.post(f"/api/game-forge/games/{game.id}/build")
    assert built.status_code == 200
    body = built.json()
    assert body["world_events_runtime"] is True
    assert body["verified_same_origin_world_audio"] is True
    assert body["external_world_audio_urls_allowed"] is False
    assert body["runtime_network_access"] is False
    assert body["runtime"] == "aura_game_runtime_2d_canvas_v5_world_events"
    assert body["world_events_editor_url"] == f"/game-creation/world-events/{game.id}"

    deleted = client.delete(f"/api/game-forge/games/{game.id}/world-events/entities/{event_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_aura_world_event_tools_register_without_duplicate_names():
    install_aura_world_events_tools()
    names = [spec.name for spec in aura_tools.TOOL_SPECS]
    for name in (
        "inspect_game_world_events",
        "create_game_world_event",
        "apply_game_world_event_preset",
        "delete_game_world_event",
        "build_world_events_playtest",
    ):
        assert name in names
    assert "inspect_game_world_logic" in names
    assert len(names) == len(set(names))
