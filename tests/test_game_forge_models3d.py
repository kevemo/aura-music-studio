from __future__ import annotations

import base64
import json
import struct
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_aura_commands import execute_game_aura_command, router as aura_command_router
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_mesh import GameModelError, extract_static_mesh
from aura_music_studio.game_forge_model_assets import list_game_models
from aura_music_studio.game_forge_model_bindings import model_binding_runtime_payload
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import render_aura3d_playtest
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP, build_private_playtest
from aura_music_studio.game_forge_world import generate_foundation_world, load_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="model3d-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _triangle_binary() -> bytes:
    positions = struct.pack(
        "<9f",
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
    )
    normals = struct.pack(
        "<9f",
        0.0, 0.0, 1.0,
        0.0, 0.0, 1.0,
        0.0, 0.0, 1.0,
    )
    uvs = struct.pack("<6f", 0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    indices = struct.pack("<3H", 0, 1, 2)
    return positions + normals + uvs + indices


def _triangle_document(*, uri: str) -> dict:
    return {
        "asset": {"version": "2.0", "generator": "Aura test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "translation": [2.0, 0.0, -1.0]}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                        "indices": 3,
                        "mode": 4,
                    }
                ]
            }
        ],
        "buffers": [{"byteLength": len(_triangle_binary()), "uri": uri}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 36},
            {"buffer": 0, "byteOffset": 72, "byteLength": 24},
            {"buffer": 0, "byteOffset": 96, "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }


def _embedded_gltf_bytes() -> bytes:
    data_uri = "data:application/octet-stream;base64," + base64.b64encode(_triangle_binary()).decode("ascii")
    return json.dumps(_triangle_document(uri=data_uri), separators=(",", ":")).encode("utf-8")


def _glb_bytes() -> bytes:
    binary = _triangle_binary()
    document = _triangle_document(uri="")
    document["buffers"] = [{"byteLength": len(binary)}]
    raw_json = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_padding = (4 - len(raw_json) % 4) % 4
    json_chunk = raw_json + b" " * json_padding
    bin_padding = (4 - len(binary) % 4) % 4
    bin_chunk = binary + b"\x00" * bin_padding
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )


def _app():
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(aura_command_router)
    return app


def _game(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Aura Model Lab",
        prompt="A native 3D world with verified imported geometry",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    generate_foundation_world(game)
    return game


def _upload_model(client: TestClient, game: GameDNA, *, filename: str = "triangle.gltf", payload: bytes | None = None, label: str = "Triangle Knight"):
    response = client.post(
        f"/api/game-forge/games/{game.id}/models",
        files={"file": (filename, payload if payload is not None else _embedded_gltf_bytes(), "model/gltf+json")},
        data={
            "label": label,
            "role": "character model",
            "rights_confirmed": "true",
            "rights_attestation": "Original test model with publishing rights confirmed.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["model"]


def test_closed_gltf_parser_flattens_transform_and_preserves_uvs(tmp_path):
    path = tmp_path / "triangle.gltf"
    path.write_bytes(_embedded_gltf_bytes())

    mesh = extract_static_mesh(path)

    assert mesh["source_format"] == "gltf"
    assert mesh["vertex_stride_floats"] == 8
    assert mesh["vertex_count"] == 3
    assert mesh["triangle_count"] == 1
    assert mesh["primitive_count"] == 1
    assert mesh["external_resources_allowed"] is False
    assert mesh["generated_code_executed"] is False
    assert mesh["vertices"][:8] == [2.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    assert mesh["bounds"]["min"] == [2.0, 0.0, -1.0]
    assert mesh["bounds"]["max"] == [3.0, 1.0, -1.0]


def test_closed_glb_parser_accepts_embedded_binary_chunk(tmp_path):
    path = tmp_path / "triangle.glb"
    path.write_bytes(_glb_bytes())

    mesh = extract_static_mesh(path)

    assert mesh["source_format"] == "glb"
    assert mesh["vertex_count"] == 3
    assert mesh["triangle_count"] == 1
    assert mesh["embedded_materials_ignored"] is True


def test_gltf_external_buffer_url_is_rejected(tmp_path):
    path = tmp_path / "external.gltf"
    path.write_text(
        json.dumps(_triangle_document(uri="https://example.invalid/model.bin")),
        encoding="utf-8",
    )

    with pytest.raises(GameModelError, match="external buffer URLs are not allowed"):
        extract_static_mesh(path)


def test_authenticated_model_upload_binding_integrity_and_v4_renderer(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    before = game_integrity_hash(game)
    client = TestClient(_app())
    model = _upload_model(client, game)

    assert model["kind"] == "model"
    assert model["raw_model_browser_url"] is None
    assert model["filesystem_path_exposed"] is False
    assert model["mesh_summary"]["vertex_count"] == 3
    assert game_integrity_hash(game) != before

    response = client.post(
        f"/api/game-forge/games/{game.id}/model-bindings",
        json={"model_id": model["id"], "entity_id": "player"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bindings"]["player"]["model_id"] == model["id"]

    world = load_world(game.id)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)
    assert "Aura Game Engine 3D v4" in html
    assert model["id"] in html
    assert "layout(location=2) in vec2 aUV" in html
    assert "makeGeometry" in html
    assert "geometryForEntity" in html
    assert "modelBindings" in html
    assert '"model_runtime_loading": false' in html
    assert '"model_external_resources": false' in html
    assert '"skeletal_animation": false' in html
    assert '"declarative_cinematics": true' in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html

    game, built_html = build_private_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v4"
    assert model["id"] in built_html


def test_aura_command_assigns_exact_3d_model_and_delete_cleans_binding(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    client = TestClient(_app())
    model = _upload_model(client, game, label="Triangle Knight")

    result = execute_game_aura_command(game, "Use Triangle Knight as the 3D model on Player")

    assert result.action == "bind"
    assert result.needs_clarification is False
    assert result.parsed["target"] == "entity_model"
    assert result.parsed["entity_id"] == "player"
    assert result.parsed["asset_id"] == model["id"]
    assert model_binding_runtime_payload(game.id)["player"] == model["id"]

    response = client.delete(f"/api/game-forge/games/{game.id}/models/{model['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["bindings_removed"] is True
    assert model_binding_runtime_payload(game.id) == {}
    assert list_game_models(game.id) == []


def test_model_binding_rejects_non_renderable_world_entity(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    client = TestClient(_app())
    model = _upload_model(client, game)

    response = client.post(
        f"/api/game-forge/games/{game.id}/model-bindings",
        json={"model_id": model["id"], "entity_id": "camera"},
    )

    assert response.status_code == 400
    assert "cannot render a 3D model" in response.json()["detail"]
