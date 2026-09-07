import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.game_forge_godot_export as godot_export
import aura_music_studio.game_forge_godot_export_api as godot_api
from aura_music_studio.game_forge_models import GameBuild, GameDNA
from aura_music_studio.game_forge_world import GameWorldDNA, WorldEntityDNA


def _game(content_hash: str = "a" * 64, *, dimension: str = "2d") -> GameDNA:
    game = GameDNA(
        id="game_godotpreview",
        title='Godot "Preview" Test',
        prompt="creator text must remain data",
        genre="adventure",
        dimension=dimension,
        engine_target="aura2d" if dimension == "2d" else "aura3d",
        rights_confirmed=True,
        rights_attestation="I own or have permission to use this material.",
    )
    game.latest_build = GameBuild(
        content_hash=content_hash,
        requested_engine=game.engine_target,
        runtime="aura_game_runtime_test",
    )
    return game


def _world(game: GameDNA) -> GameWorldDNA:
    return GameWorldDNA(
        game_id=game.id,
        dimension=game.dimension,
        entities=[
            WorldEntityDNA(id="player", name="Player", kind="player"),
            WorldEntityDNA(id="crate", name="Crate", kind="mesh"),
        ],
    )


def _prepare(monkeypatch, tmp_path: Path, game: GameDNA, content_hash: str):
    monkeypatch.setattr(godot_export, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(godot_export, "asset_publication_blockers", lambda _game_id: [])
    monkeypatch.setattr(godot_export, "ensure_world", lambda _game: _world(game))
    monkeypatch.setattr(godot_export, "runtime_asset_manifest", lambda _game_id: [])
    monkeypatch.setattr(godot_export, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")


def test_source_export_requires_rights_and_current_aura_build(monkeypatch):
    game = _game()
    monkeypatch.setattr(godot_export, "game_integrity_hash", lambda _game: game.latest_build.content_hash)
    monkeypatch.setattr(godot_export, "asset_publication_blockers", lambda _game_id: [])
    assert godot_export._validate_source_exportable(game) == game.latest_build.content_hash

    game.rights_confirmed = False
    with pytest.raises(ValueError, match="rights"):
        godot_export._validate_source_exportable(game)

    game.rights_confirmed = True
    monkeypatch.setattr(godot_export, "game_integrity_hash", lambda _game: "b" * 64)
    with pytest.raises(ValueError, match="stale"):
        godot_export._validate_source_exportable(game)


def test_godot_source_zip_is_deterministic_and_truthful(tmp_path: Path, monkeypatch):
    content_hash = "c" * 64
    game = _game(content_hash)
    _prepare(monkeypatch, tmp_path, game, content_hash)

    first = godot_export.create_godot_source_export(game)
    first_bytes = (tmp_path / first["filename"]).read_bytes()
    second = godot_export.create_godot_source_export(game)
    second_bytes = (tmp_path / second["filename"]).read_bytes()

    assert first["export_id"] == second["export_id"]
    assert first["sha256"] == second["sha256"]
    assert first_bytes == second_bytes
    assert first["source_project_ready"] is True
    assert first["production_ready"] is False
    assert first["runtime_parity_claimed"] is False
    assert first["creator_generated_executable_code"] is False

    with ZipFile(tmp_path / first["filename"]) as zf:
        assert set(zf.namelist()) == {
            "project.godot",
            "main.tscn",
            "main.gd",
            "README.md",
            "adapter_manifest.json",
            "game_dna.json",
            "world_dna.json",
            "assets.json",
        }
        project = zf.read("project.godot").decode("utf-8")
        assert "config_version=5" in project
        assert 'run/main_scene="res://main.tscn"' in project
        assert 'config/name="Godot \\"Preview\\" Test"' in project
        scene = zf.read("main.tscn").decode("utf-8")
        assert scene.startswith("[gd_scene load_steps=2 format=3]")
        assert 'path="res://main.gd"' in scene
        script = zf.read("main.gd").decode("utf-8")
        assert script.startswith("extends Node")
        assert '_load_json("res://game_dna.json")' in script
        assert '_load_json("res://world_dna.json")' in script
        assert "FileAccess.get_file_as_string(path)" in script
        assert "JSON.parse_string" in script
        assert "creator text must remain data" not in script
        manifest = json.loads(zf.read("adapter_manifest.json"))
        assert manifest["adapter"] == "godot4_source_preview"
        assert manifest["production_ready"] is False
        assert manifest["fixed_reviewed_gdscript_template"] is True
        assert manifest["creator_generated_executable_code"] is False
        assert manifest["requires_headless_engine_validation_before_production"] is True
        game_data = json.loads(zf.read("game_dna.json"))
        assert game_data["content_hash"] == content_hash
        world_data = json.loads(zf.read("world_dna.json"))
        assert {row["id"] for row in world_data["entities"]} == {"player", "crate"}
        assert "world_id" not in world_data
        assert "created_at" not in world_data
        assert "updated_at" not in world_data
        readme = zf.read("README.md").decode("utf-8")
        assert "godot --headless --path . --quit-after 1" in readme
        assert "not a production-equivalent port" in readme


def test_3d_preview_data_selects_3d_without_generated_creator_script(tmp_path: Path, monkeypatch):
    content_hash = "d" * 64
    game = _game(content_hash, dimension="3d")
    _prepare(monkeypatch, tmp_path, game, content_hash)
    result = godot_export.create_godot_source_export(game)
    with ZipFile(tmp_path / result["filename"]) as zf:
        data = json.loads(zf.read("game_dna.json"))
        assert data["dimension"] == "3d"
        script = zf.read("main.gd").decode("utf-8")
        assert "func _build_3d()" in script
        assert "MeshInstance3D.new()" in script
        assert game.prompt not in script


def test_verified_media_bytes_are_copied_and_checksum_mismatch_fails(tmp_path: Path, monkeypatch):
    content_hash = "e" * 64
    game = _game(content_hash)
    world = _world(game)
    media = tmp_path / "asset.wav"
    media.write_bytes(b"RIFF-godot-media")
    digest = godot_export._sha256_bytes(media.read_bytes())
    row = {
        "id": "asset_test",
        "kind": "audio",
        "label": "Verified media",
        "role": "ambient",
        "media_url": "media/asset_test.wav",
        "mime_type": "audio/wav",
        "sha256": digest,
        "byte_size": media.stat().st_size,
    }
    monkeypatch.setattr(godot_export, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(godot_export, "asset_publication_blockers", lambda _game_id: [])
    monkeypatch.setattr(godot_export, "ensure_world", lambda _game: world)
    monkeypatch.setattr(godot_export, "runtime_asset_manifest", lambda _game_id: [row])
    monkeypatch.setattr(
        godot_export,
        "private_runtime_asset_path",
        lambda *_: (media, SimpleNamespace(label="Verified media")),
    )
    monkeypatch.setattr(godot_export, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")
    result = godot_export.create_godot_source_export(game)
    with ZipFile(tmp_path / result["filename"]) as zf:
        assert zf.read("media/asset_test.wav") == media.read_bytes()
        assets = json.loads(zf.read("assets.json"))
        assert assets[0]["sha256"] == digest

    row["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="integrity verification"):
        godot_export.create_godot_source_export(game)


def test_capability_and_portal_keep_preview_not_production(monkeypatch):
    game = _game()
    monkeypatch.setattr(godot_api, "load_game", lambda _game_id: game)
    app = FastAPI()
    app.include_router(godot_api.router)

    @app.middleware("http")
    async def fake_member(request, call_next):
        request.state.member = SimpleNamespace(plan=SimpleNamespace(has=lambda _cap: True))
        return await call_next(request)

    with TestClient(app) as client:
        capability = client.get(f"/api/game-forge/games/{game.id}/exports/godot-source/capability")
        assert capability.status_code == 200
        body = capability.json()
        assert body["source_project_ready"] is True
        assert body["production_ready"] is False
        assert body["runtime_parity_claimed"] is False
        page = client.get(f"/game-creation/godot-export/{game.id}")
        assert page.status_code == 200
        assert "Source-ready ≠ production parity" in page.text
        assert "Create Godot Source ZIP" in page.text


def test_anonymous_godot_portal_redirects_to_signin():
    app = FastAPI()
    app.include_router(godot_api.router)
    with TestClient(app) as client:
        response = client.get("/game-creation/godot-export/game_demo", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/signin?next=/game-creation/godot-export/")