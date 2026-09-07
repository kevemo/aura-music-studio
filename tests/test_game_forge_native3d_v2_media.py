from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_assets as assets
from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_asset_bindings import (
    BindGameAssetRequest,
    binding_publication_blockers,
    binding_runtime_payload,
    bind_game_asset,
    clear_asset_bindings,
)
from aura_music_studio.game_forge_aura_commands import execute_game_aura_command
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import render_aura3d_playtest
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP
from aura_music_studio.game_forge_world import (
    BehaviorNodeDNA,
    MaterialDNA,
    TransformDNA,
    Vec3,
    WorldEntityDNA,
    generate_foundation_world,
    load_world,
    save_world,
)
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="native3d-v2-user")


def _patch_storage(monkeypatch, tmp_path):
    root = tmp_path / "games"
    public = tmp_path / "public"
    root.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return root, public


def _install_asset(game: GameDNA, *, label: str, kind: str, suffix: str, role: str, payload: bytes):
    digest = hashlib.sha256(payload).hexdigest()
    record = assets.GameAssetRecord(
        game_id=game.id,
        kind=kind,
        label=label,
        role=role,
        source_type="generated",
        source_project="native3d-v2-test",
        source_element_id=f"source-{digest[:12]}",
        source_element_updated_at="2026-08-27T00:00:00+00:00",
        source_media_sha256=digest,
        imported_filename="pending",
        byte_size=len(payload),
        rights_confirmed=True,
        rights_attestation="Original test media with publishing rights confirmed.",
    )
    record.imported_filename = f"{record.id}{suffix}"
    assets._asset_file(game.id, record).write_bytes(payload)
    manifest = assets.load_asset_manifest(game.id)
    manifest.assets.append(record)
    assets.save_asset_manifest(manifest)
    return record


def _game_with_render_entity(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Aura3D PBR Lab",
        prompt="A native 3D material and spatial audio validation world",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    world = generate_foundation_world(game)
    world.entities.append(
        WorldEntityDNA(
            id="pbr_crate",
            name="PBR Crate",
            kind="mesh",
            transform=TransformDNA(position=Vec3(x=3, y=1, z=-2)),
            material=MaterialDNA(
                shader="pbr",
                base_color="#d9d9e6",
                metallic=0.65,
                roughness=0.42,
                emissive_strength=0.2,
                opacity=0.92,
            ),
        )
    )
    world.entities.append(
        WorldEntityDNA(
            id="waterfall_audio",
            name="Waterfall",
            kind="audio",
            transform=TransformDNA(position=Vec3(x=8, y=2, z=-12)),
            behaviors=[
                BehaviorNodeDNA(
                    op="audio_zone",
                    params={"loop": True, "volume": 0.8, "ref_distance": 3, "max_distance": 120, "rolloff": 1.25},
                )
            ],
        )
    )
    save_world(world)
    return game


def test_aura3d_v2_consumes_all_supported_pbr_maps(monkeypatch, tmp_path):
    game = _game_with_render_entity(monkeypatch, tmp_path)
    slots = ("base_color", "normal", "metallic", "roughness", "emissive", "opacity", "ao", "height")
    records = {}
    for slot in slots:
        record = _install_asset(
            game,
            label=f"Crate {slot}",
            kind="image",
            suffix=".png",
            role=f"{slot} texture",
            payload=f"verified-{slot}".encode(),
        )
        records[slot] = record
        bind_game_asset(
            game,
            BindGameAssetRequest(
                asset_id=record.id,
                target="entity_texture",
                entity_id="pbr_crate",
                material_slot=slot,
            ),
        )

    world = load_world(game.id)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)

    for record in records.values():
        assert f"media/{record.imported_filename}" in html
    for name in (
        "uBaseColorMap",
        "uNormalMap",
        "uMetallicMap",
        "uRoughnessMap",
        "uEmissiveMap",
        "uOpacityMap",
        "uAOMap",
        "uHeightMap",
    ):
        assert name in html
    assert "distributionGGX" in html
    assert "geometrySmith" in html
    assert "fresnelSchlick" in html
    assert "cotangentFrame" in html
    assert '"height_map_mode": "micro_parallax"' in html
    assert '"pbr_material_maps": ["base_color", "normal", "metallic", "roughness", "emissive", "opacity", "ao", "height"]' in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html


def test_entity_audio_binding_is_exact_integrity_bound_and_spatial(monkeypatch, tmp_path):
    game = _game_with_render_entity(monkeypatch, tmp_path)
    sound = _install_asset(
        game,
        label="Waterfall Roar",
        kind="audio",
        suffix=".ogg",
        role="spatial sfx",
        payload=b"verified-waterfall-audio",
    )

    state = bind_game_asset(
        game,
        BindGameAssetRequest(asset_id=sound.id, target="entity_audio", entity_id="waterfall_audio"),
    )
    assert state["raw_refs"]["entities"]["waterfall_audio"]["audio"] == sound.id
    assert state["bindings"]["entities"]["waterfall_audio"]["audio"]["id"] == sound.id
    assert binding_publication_blockers(game.id) == []

    world = load_world(game.id)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)
    assert f"media/{sound.imported_filename}" in html
    assert "createPanner()" in html
    assert "panner.panningModel='HRTF'" in html
    assert "createMediaElementSource" in html
    assert "musicGain=audioContext.createGain()" in html
    assert "sfxGain=audioContext.createGain()" in html
    assert "setListener(eye,target)" in html
    assert "setAudioPosition(s.panner" in html
    assert '"spatial_audio": true' in html
    assert '"separate_music_and_sfx_buses": true' in html
    assert "connect-src 'none'" in html


def test_aura_plain_language_can_assign_spatial_audio_to_exact_entity(monkeypatch, tmp_path):
    game = _game_with_render_entity(monkeypatch, tmp_path)
    sound = _install_asset(
        game,
        label="Waterfall Roar",
        kind="audio",
        suffix=".wav",
        role="world sound",
        payload=b"waterfall-command-audio",
    )

    result = execute_game_aura_command(game, "Use Waterfall Roar as spatial audio on Waterfall")

    assert result.action == "bind"
    assert result.needs_clarification is False
    assert result.parsed["target"] == "entity_audio"
    assert result.parsed["entity_id"] == "waterfall_audio"
    assert result.parsed["asset_id"] == sound.id
    payload = binding_runtime_payload(game.id)
    assert payload["entities"]["waterfall_audio"]["audio"]["id"] == sound.id


def test_entity_audio_rejects_non_audio_and_is_removed_with_asset_cleanup(monkeypatch, tmp_path):
    game = _game_with_render_entity(monkeypatch, tmp_path)
    image = _install_asset(
        game,
        label="Wrong Kind",
        kind="image",
        suffix=".webp",
        role="texture",
        payload=b"not-audio",
    )
    with pytest.raises(ValueError, match="entity_audio requires"):
        bind_game_asset(
            game,
            BindGameAssetRequest(asset_id=image.id, target="entity_audio", entity_id="waterfall_audio"),
        )

    sound = _install_asset(
        game,
        label="Temporary Sound",
        kind="audio",
        suffix=".mp3",
        role="sfx",
        payload=b"temporary-audio",
    )
    bind_game_asset(
        game,
        BindGameAssetRequest(asset_id=sound.id, target="entity_audio", entity_id="waterfall_audio"),
    )
    assert clear_asset_bindings(game.id, sound.id) is True
    world = load_world(game.id)
    entity = next(row for row in world.entities if row.id == "waterfall_audio")
    assert "game_audio_asset_ref" not in entity.metadata
    assert "waterfall_audio" not in binding_runtime_payload(game.id)["entities"]
