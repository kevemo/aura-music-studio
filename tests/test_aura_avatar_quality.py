import json
import struct
from pathlib import Path

from aura_music_studio.aura_avatar_quality import (
    ARKIT_FACE_MORPHS,
    PRODUCTION_HUMANOID_BONES,
    REQUIRED_ANIMATIONS,
    REQUIRED_AURA_CUSTOM_EXPRESSIONS,
    REQUIRED_VRM_PRESET_EXPRESSIONS,
    validate_aura_production_model,
)


def _write_glb(path: Path, document: dict) -> None:
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((-len(raw)) % 4)
    total = 12 + 8 + len(raw)
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, total))
        handle.write(struct.pack("<II", len(raw), 0x4E4F534A))
        handle.write(raw)


def _production_document() -> dict:
    bone_names = sorted(PRODUCTION_HUMANOID_BONES)
    bones = {name: {"node": index} for index, name in enumerate(bone_names)}
    nodes = [{"name": name} for name in bone_names]
    preset = {name: {} for name in REQUIRED_VRM_PRESET_EXPRESSIONS}
    custom = {name: {} for name in REQUIRED_AURA_CUSTOM_EXPRESSIONS}
    morphs = sorted(ARKIT_FACE_MORPHS)
    return {
        "asset": {"version": "2.0", "generator": "Aura production test"},
        "extensionsUsed": [
            "VRMC_vrm",
            "VRMC_springBone",
            "KHR_texture_basisu",
            "KHR_meshopt_compression",
            "KHR_materials_emissive_strength",
        ],
        "extensions": {
            "VRMC_vrm": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": bones},
                "expressions": {"preset": preset, "custom": custom},
                "lookAt": {"type": "bone"},
                "meta": {"name": "Aura"},
            },
            "VRMC_springBone": {
                "specVersion": "1.0",
                "springs": [{"name": "Aura_Hair", "joints": [{"node": 0}]}],
            },
        },
        "nodes": nodes,
        "skins": [{"joints": list(range(min(8, len(nodes))))}],
        "accessors": [{"count": 300000}, {"count": 90000}],
        "meshes": [
            {
                "name": "Aura_Face",
                "extras": {"targetNames": morphs},
                "primitives": [{"mode": 4, "indices": 1, "attributes": {"POSITION": 0}}],
            }
        ],
        "materials": [
            {"name": "Aura_Skin"},
            {"name": "Aura_Eyes", "emissiveFactor": [1.0, 0.1, 0.8]},
            {"name": "Aura_Hair"},
            {"name": "Aura_Suit"},
            {
                "name": "Aura_Heart_Core",
                "emissiveFactor": [1.0, 0.05, 0.5],
                "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 2.0}},
            },
            {
                "name": "Aura_Circuitry",
                "emissiveFactor": [0.5, 0.05, 1.0],
                "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 1.5}},
            },
        ],
        "images": [{"mimeType": "image/ktx2", "bufferView": 0}],
        "animations": [{"name": name} for name in sorted(REQUIRED_ANIMATIONS)],
    }


def test_complete_aura_contract_passes_strict_production_gate(tmp_path: Path):
    model = tmp_path / "aura.glb"
    _write_glb(model, _production_document())
    result = validate_aura_production_model(model)
    assert result["production_ready"] is True
    assert result["blocking_reasons"] == []
    assert result["arkit_face_morph_coverage"] == 1.0
    assert result["has_vrm_look_at"] is True
    assert result["has_vrm_spring_bone"] is True
    assert result["emissive_semantics"] == {"heart_core": True, "circuitry": True}
    assert result["ktx2_basis"] is True
    assert result["mesh_compression"] is True


def test_missing_finger_bone_blocks_production(tmp_path: Path):
    document = _production_document()
    document["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"].pop("leftIndexDistal")
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_production_model(model)
    assert result["production_ready"] is False
    assert "leftIndexDistal" in result["missing_production_humanoid_bones"]


def test_missing_detailed_face_morph_blocks_production(tmp_path: Path):
    document = _production_document()
    document["meshes"][0]["extras"]["targetNames"].remove("jawOpen")
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_production_model(model)
    assert result["production_ready"] is False
    assert "jawOpen" in result["missing_arkit_face_morphs"]


def test_missing_lookat_or_hair_physics_blocks_production(tmp_path: Path):
    document = _production_document()
    document["extensions"]["VRMC_vrm"].pop("lookAt")
    document["extensions"]["VRMC_springBone"]["springs"] = []
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_production_model(model)
    assert result["production_ready"] is False
    assert result["has_vrm_look_at"] is False
    assert result["has_vrm_spring_bone"] is False


def test_missing_mobile_compression_blocks_production(tmp_path: Path):
    document = _production_document()
    document["extensionsUsed"] = [
        value for value in document["extensionsUsed"]
        if value not in {"KHR_texture_basisu", "KHR_meshopt_compression"}
    ]
    document["images"] = []
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_production_model(model)
    assert result["production_ready"] is False
    assert result["ktx2_basis"] is False
    assert result["mesh_compression"] is False


def test_heart_and_circuitry_must_be_emissive(tmp_path: Path):
    document = _production_document()
    for material in document["materials"]:
        if material["name"] in {"Aura_Heart_Core", "Aura_Circuitry"}:
            material.pop("emissiveFactor", None)
            material.pop("extensions", None)
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_production_model(model)
    assert result["production_ready"] is False
    assert result["emissive_semantics"] == {"heart_core": False, "circuitry": False}
