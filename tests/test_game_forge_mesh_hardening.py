from __future__ import annotations

import base64
import json
import math
import struct

import pytest

from aura_music_studio.game_forge_mesh import (
    GameModelError,
    _MAX_ACCESSOR_ELEMENTS,
    _MAX_NODES,
    _validate_document_shape,
    extract_static_mesh,
)
from aura_music_studio.game_forge_model_assets import _runtime_capabilities


def _triangle_document(binary: bytes) -> dict:
    uri = "data:application/octet-stream;base64," + base64.b64encode(binary).decode("ascii")
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary), "uri": uri}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
    }


def _write_gltf(tmp_path, document: dict, name: str = "model.gltf"):
    path = tmp_path / name
    path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    return path


def test_closed_gltf_rejects_non_finite_geometry_before_runtime_projection(tmp_path):
    binary = struct.pack(
        "<9f",
        0.0,
        0.0,
        0.0,
        math.nan,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
    )
    path = _write_gltf(tmp_path, _triangle_document(binary))

    with pytest.raises(GameModelError, match="non-finite numeric values"):
        extract_static_mesh(path)


def test_closed_gltf_rejects_non_finite_node_transform(tmp_path):
    binary = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    document = _triangle_document(binary)
    document["nodes"][0]["translation"] = [math.inf, 0.0, 0.0]
    path = _write_gltf(tmp_path, document)

    with pytest.raises(GameModelError, match="non-finite numeric values"):
        extract_static_mesh(path)


def test_document_shape_rejects_oversized_accessor_before_buffer_decode():
    document = {
        "nodes": [],
        "meshes": [],
        "buffers": [],
        "bufferViews": [],
        "accessors": [{"count": _MAX_ACCESSOR_ELEMENTS + 1}],
    }

    with pytest.raises(GameModelError, match="element safety limit"):
        _validate_document_shape(document)


def test_document_shape_rejects_excessive_node_count_without_traversal():
    document = {
        "nodes": [{}] * (_MAX_NODES + 1),
        "meshes": [],
        "buffers": [],
        "bufferViews": [],
        "accessors": [],
    }

    with pytest.raises(GameModelError, match="nodes safety limit"):
        _validate_document_shape(document)


def test_static_projection_truthfully_reports_present_animation_and_skin_as_not_executed(tmp_path):
    binary = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    document = _triangle_document(binary)
    document["animations"] = [{"name": "Walk"}]
    document["skins"] = [{"name": "Rig"}]
    path = _write_gltf(tmp_path, document)

    mesh = extract_static_mesh(path)

    assert mesh["animations_present"] is True
    assert mesh["skins_present"] is True
    assert mesh["static_projection_only"] is True
    assert mesh["animation_clips_executed"] is False
    assert mesh["skinning_executed"] is False
    assert mesh["generated_code_executed"] is False
    assert mesh["external_resources_allowed"] is False


def test_model_runtime_capabilities_warn_without_claiming_rig_or_clip_execution():
    capabilities = _runtime_capabilities({"animations_present": True, "skins_present": True})

    assert capabilities["projection_mode"] == "closed_static_mesh"
    assert capabilities["runtime_mesh_projection"] is True
    assert capabilities["skeletal_animation_runtime"] is False
    assert capabilities["skinning_runtime"] is False
    assert capabilities["animation_clips_runtime"] is False
    assert capabilities["source_animation_or_skin_data_executed"] is False
    assert capabilities["external_resources_allowed"] is False
    assert capabilities["runtime_network_required"] is False
    assert len(capabilities["warnings"]) == 2
    assert "not executed" in capabilities["warnings"][0]
    assert "not executed" in capabilities["warnings"][1]
