from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_assets as assets
from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_asset_bindings import (
    BindGameAssetRequest,
    UnbindGameAssetRequest,
    bind_game_asset,
    binding_publication_blockers,
    binding_runtime_payload,
    clear_asset_bindings,
    unbind_game_asset,
)
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import render_aura3d_playtest
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP, render_foundation_playtest
from aura_music_studio.game_forge_world import ensure_world, load_world, save_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="binding-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _asset(game: GameDNA, *, kind: str, suffix: str, role: str, payload: bytes):
    digest = hashlib.sha256(payload).hexdigest()
    record = assets.GameAssetRecord(
        game_id=game.id,
        kind=kind,
        label=f"Bound {kind}",
        role=role,
        source_type="generated",
        source_project="binding-test",
        source_element_id=f"source-{kind}-{digest[:8]}",
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


def test_explicit_bindings_live_in_world_dna_and_change_integrity(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Bound World",
        prompt="A world with explicit creative asset assignments",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    texture = _asset(game, kind="image", suffix=".png", role="generic", payload=b"ground-texture")
    soundtrack = _asset(game, kind="music", suffix=".wav", role="generic", payload=b"soundtrack")
    before = game_integrity_hash(game)

    bind_game_asset(
        game,
        BindGameAssetRequest(
            asset_id=texture.id,
            target="entity_texture",
            entity_id="ground",
            material_slot="base_color",
        ),
    )
    bind_game_asset(game, BindGameAssetRequest(asset_id=soundtrack.id, target="soundtrack"))

    world = load_world(game.id)
    ground = next(row for row in world.entities if row.id == "ground")
    assert ground.material is not None
    assert ground.material.texture_refs["base_color"] == texture.id
    assert world.metadata["game_asset_bindings"]["soundtrack"] == soundtrack.id
    assert game_integrity_hash(game) != before

    runtime = binding_runtime_payload(game.id, world=world)
    assert runtime["entities"]["ground"]["textures"]["base_color"]["id"] == texture.id
    assert runtime["world"]["soundtrack"]["id"] == soundtrack.id
    assert runtime["entities"]["ground"]["textures"]["base_color"]["media_url"].startswith("media/")


def test_binding_type_safety_rejects_audio_as_texture_and_image_as_cutscene(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Typed Bindings", prompt="Typed assets", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    ensure_world(game)
    audio = _asset(game, kind="audio", suffix=".ogg", role="sfx", payload=b"audio")
    image = _asset(game, kind="image", suffix=".webp", role="texture", payload=b"image")

    with pytest.raises(ValueError, match="requires an image"):
        bind_game_asset(game, BindGameAssetRequest(asset_id=audio.id, target="entity_texture", entity_id="ground"))
    with pytest.raises(ValueError, match="requires a video"):
        bind_game_asset(game, BindGameAssetRequest(asset_id=image.id, target="cutscene"))


def test_aura2d_prefers_explicit_background_soundtrack_cutscene_and_player_visual(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Bound 2D", prompt="Explicit 2D media", dimension="2d", engine_target="aura2d")
    store.create_game(_member(), game)
    ensure_world(game)
    background = _asset(game, kind="image", suffix=".jpg", role="generic", payload=b"background")
    player = _asset(game, kind="image", suffix=".png", role="generic", payload=b"player")
    soundtrack = _asset(game, kind="music", suffix=".mp3", role="generic", payload=b"music")
    cutscene = _asset(game, kind="video", suffix=".webm", role="generic", payload=b"video")

    bind_game_asset(game, BindGameAssetRequest(asset_id=background.id, target="world_background"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=player.id, target="entity_visual", entity_id="player"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=soundtrack.id, target="soundtrack"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=cutscene.id, target="cutscene"))

    html = render_foundation_playtest(game)
    assert f"media/{background.imported_filename}" in html
    assert f"media/{player.imported_filename}" in html
    assert f"media/{soundtrack.imported_filename}" in html
    assert f"media/{cutscene.imported_filename}" in html
    assert "worldBindings.world_background" in html
    assert "entityBindings.player?.visual" in html
    assert "drawPlayer" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html


def test_aura3d_uses_explicit_per_entity_texture_bindings(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Bound 3D", prompt="Entity textures", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    ensure_world(game)
    ground = _asset(game, kind="image", suffix=".png", role="generic", payload=b"ground")
    player = _asset(game, kind="image", suffix=".webp", role="generic", payload=b"player")

    bind_game_asset(game, BindGameAssetRequest(asset_id=ground.id, target="entity_texture", entity_id="ground", material_slot="base_color"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=player.id, target="entity_visual", entity_id="player"))
    world = load_world(game.id)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)

    assert f"media/{ground.imported_filename}" in html
    assert f"media/{player.imported_filename}" in html
    assert "materialAsset(e,slot)" in html
    assert "b.textures?.base_color||b.visual" in html
    assert "textureStates=new Map()" in html
    assert '"explicit_world_dna_bindings": true' in html
    assert "gl.enable(gl.DEPTH_TEST)" in html
    assert "gl.enable(gl.CULL_FACE)" in html
    assert "navigator.gpu" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html


def test_unbind_and_clear_remove_world_references(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Clear Bindings", prompt="Clear refs", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    ensure_world(game)
    image = _asset(game, kind="image", suffix=".png", role="generic", payload=b"image")
    music = _asset(game, kind="music", suffix=".wav", role="generic", payload=b"music")

    bind_game_asset(game, BindGameAssetRequest(asset_id=image.id, target="entity_visual", entity_id="player"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=image.id, target="entity_texture", entity_id="ground"))
    bind_game_asset(game, BindGameAssetRequest(asset_id=music.id, target="soundtrack"))

    unbind_game_asset(game, UnbindGameAssetRequest(target="soundtrack"))
    assert "soundtrack" not in load_world(game.id).metadata["game_asset_bindings"]

    assert clear_asset_bindings(game.id, image.id) is True
    world = load_world(game.id)
    player = next(row for row in world.entities if row.id == "player")
    ground = next(row for row in world.entities if row.id == "ground")
    assert player.asset_ref is None
    assert ground.material is not None
    assert "base_color" not in ground.material.texture_refs


def test_dangling_and_wrong_type_world_refs_block_publication(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Binding Guard", prompt="Guard refs", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    world = ensure_world(game)
    world.metadata["game_asset_bindings"] = {"soundtrack": "asset_missing"}
    ground = next(row for row in world.entities if row.id == "ground")
    assert ground.material is not None
    ground.material.texture_refs["base_color"] = "asset_missing_texture"
    world.touch()
    save_world(world)

    blockers = " ".join(binding_publication_blockers(game.id)).lower()
    assert "soundtrack" in blockers
    assert "missing asset" in blockers
    assert "material slot" in blockers


def test_binding_aware_delete_route_clears_world_refs_before_asset_deletion(monkeypatch, tmp_path):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from aura_music_studio.game_forge_world_api import router as world_router

    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Delete Bound Asset", prompt="Delete safely", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    ensure_world(game)
    image = _asset(game, kind="image", suffix=".png", role="player", payload=b"bound-player")
    bind_game_asset(game, BindGameAssetRequest(asset_id=image.id, target="entity_visual", entity_id="player"))
    assert next(row for row in load_world(game.id).entities if row.id == "player").asset_ref == image.id

    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    response = TestClient(app).delete(f"/api/game-forge/games/{game.id}/assets/{image.id}")

    assert response.status_code == 200
    assert response.json()["bindings_removed"] is True
    assert next(row for row in load_world(game.id).entities if row.id == "player").asset_ref is None
    with pytest.raises(FileNotFoundError):
        assets.find_game_asset(game.id, image.id)
