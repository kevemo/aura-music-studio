from __future__ import annotations

import hashlib
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import game_forge_assets as assets
from aura_music_studio import game_forge_store as store
from aura_music_studio.aura_game_tools import _explicit_game_write_allowed
from aura_music_studio.game_forge_aura_commands import execute_game_aura_command
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_portal import router as portal_router
from aura_music_studio.game_forge_world import ensure_world, load_world
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="aura-command-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _game(monkeypatch, tmp_path, *, dimension: str = "3d") -> GameDNA:
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Aura Command World",
        prompt="A creator-editable world driven by verified media",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def _asset(
    game: GameDNA,
    *,
    label: str,
    kind: str,
    suffix: str,
    role: str = "generic",
    payload: bytes,
):
    digest = hashlib.sha256(payload).hexdigest()
    record = assets.GameAssetRecord(
        game_id=game.id,
        kind=kind,
        label=label,
        role=role,
        source_type="generated",
        source_project="aura-command-test",
        source_element_id=f"source-{digest[:12]}",
        source_element_updated_at="2026-08-27T00:00:00+00:00",
        source_media_sha256=digest,
        imported_filename="pending",
        byte_size=len(payload),
        rights_confirmed=True,
        rights_attestation="Original media with publishing rights confirmed.",
    )
    record.imported_filename = f"{record.id}{suffix}"
    assets._asset_file(game.id, record).write_bytes(payload)
    manifest = assets.load_asset_manifest(game.id)
    manifest.assets.append(record)
    assets.save_asset_manifest(manifest)
    return record


def test_plain_language_commands_bind_and_unbind_exact_global_targets(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    sky = _asset(game, label="Cosmic Sky", kind="image", suffix=".png", payload=b"sky")
    song = _asset(game, label="Sparkles", kind="music", suffix=".mp3", payload=b"song")
    intro = _asset(game, label="Opening Stars", kind="video", suffix=".webm", payload=b"intro")

    background = execute_game_aura_command(game, "use Cosmic Sky as the world background")
    soundtrack = execute_game_aura_command(game, "set Sparkles as the soundtrack")
    cutscene = execute_game_aura_command(game, "use Opening Stars as the intro video cutscene")

    assert background.action == "bind"
    assert background.parsed["asset_id"] == sky.id
    assert background.parsed["target"] == "world_background"
    assert soundtrack.action == "bind"
    assert soundtrack.parsed["asset_id"] == song.id
    assert soundtrack.parsed["target"] == "soundtrack"
    assert cutscene.action == "bind"
    assert cutscene.parsed["asset_id"] == intro.id
    assert cutscene.parsed["target"] == "cutscene"

    world = load_world(game.id)
    refs = world.metadata["game_asset_bindings"]
    assert refs["world_background"] == sky.id
    assert refs["soundtrack"] == song.id
    assert refs["cutscene"] == intro.id

    removed = execute_game_aura_command(game, "remove the cutscene")
    assert removed.action == "unbind"
    assert removed.changed is True
    assert "cutscene" not in load_world(game.id).metadata["game_asset_bindings"]


def test_plain_language_entity_texture_targets_exact_entity_and_material_slot(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    texture = _asset(game, label="Neon Stone", kind="image", suffix=".webp", payload=b"texture")

    result = execute_game_aura_command(game, "apply Neon Stone to ground base color texture")

    assert result.action == "bind"
    assert result.needs_clarification is False
    assert result.parsed["asset_id"] == texture.id
    assert result.parsed["target"] == "entity_texture"
    assert result.parsed["entity_id"] == "ground"
    assert result.parsed["material_slot"] == "base_color"
    ground = next(row for row in load_world(game.id).entities if row.id == "ground")
    assert ground.material is not None
    assert ground.material.texture_refs["base_color"] == texture.id


def test_command_refuses_to_guess_between_ambiguous_matching_assets(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    first = _asset(game, label="Cosmic Sky", kind="image", suffix=".png", payload=b"sky-one")
    second = _asset(game, label="Cosmic Sky", kind="image", suffix=".webp", payload=b"sky-two")

    result = execute_game_aura_command(game, "use Cosmic Sky as the world background")

    assert result.action == "clarify"
    assert result.changed is False
    assert result.needs_clarification is True
    assert {row["id"] for row in result.candidates} == {first.id, second.id}
    assert "world_background" not in load_world(game.id).metadata.get("game_asset_bindings", {})


def test_command_requires_exact_entity_for_entity_media(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    _asset(game, label="Hero Glow", kind="image", suffix=".png", payload=b"hero")

    result = execute_game_aura_command(game, "use Hero Glow as an entity visual")

    assert result.action == "clarify"
    assert result.needs_clarification is True
    assert result.parsed["target"] == "entity_visual"
    assert result.candidates
    assert any(row["id"] == "player" for row in result.candidates)


def test_world_router_exposes_authenticated_aura_command_endpoint(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    sky = _asset(game, label="Portal Sky", kind="image", suffix=".png", payload=b"portal-sky")
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)
    response = client.post(
        f"/api/game-forge/games/{game.id}/aura-command",
        json={"command": "use Portal Sky as the world background"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "bind"
    assert body["parsed"]["asset_id"] == sky.id
    assert body["bindings"]["raw_refs"]["world"]["world_background"] == sky.id


def test_game_creation_portal_renders_media_binding_and_creative_library_controls():
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(portal_router)
    response = TestClient(app).get("/game-creation")

    assert response.status_code == 200
    html = response.text
    assert "Aura Media & World Bindings" in html
    assert "Import from Music, Video & Image Houses" in html
    assert "Assign to World DNA" in html
    assert "Apply with Aura" in html
    assert "/assets/library" in html
    assert "/asset-bindings" in html
    assert "/aura-command" in html
    assert "assetRights" in html
    assert "I own or have permission" in html


def test_aura_game_tool_write_gate_requires_explicit_matching_user_intent():
    assert _explicit_game_write_allowed(
        "apply_game_media_command",
        "Use Cosmic Sky as the game background",
    ) is True
    assert _explicit_game_write_allowed(
        "unbind_game_media_asset",
        "Remove the soundtrack from this game",
    ) is True
    assert _explicit_game_write_allowed(
        "bind_game_media_asset",
        "Tell me what assets are available",
    ) is False
    assert _explicit_game_write_allowed(
        "apply_game_media_command",
        "What do you think of my game?",
    ) is False
