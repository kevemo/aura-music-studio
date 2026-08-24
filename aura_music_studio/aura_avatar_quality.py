from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .aura_avatar import MODEL_PATH, REQUIRED_HUMANOID_BONES, _glb_json, validate_aura_model

# VRM 1.0 production rig coverage. These names follow VRMC_vrm humanoid humanBones.
PRODUCTION_HUMANOID_BONES = REQUIRED_HUMANOID_BONES | {
    "upperChest",
    "leftShoulder", "rightShoulder",
    "leftToes", "rightToes",
    "leftEye", "rightEye",
    "leftThumbMetacarpal", "leftThumbProximal", "leftThumbDistal",
    "leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal",
    "leftMiddleProximal", "leftMiddleIntermediate", "leftMiddleDistal",
    "leftRingProximal", "leftRingIntermediate", "leftRingDistal",
    "leftLittleProximal", "leftLittleIntermediate", "leftLittleDistal",
    "rightThumbMetacarpal", "rightThumbProximal", "rightThumbDistal",
    "rightIndexProximal", "rightIndexIntermediate", "rightIndexDistal",
    "rightMiddleProximal", "rightMiddleIntermediate", "rightMiddleDistal",
    "rightRingProximal", "rightRingIntermediate", "rightRingDistal",
    "rightLittleProximal", "rightLittleIntermediate", "rightLittleDistal",
}

REQUIRED_VRM_PRESET_EXPRESSIONS = {
    "blink", "blinkLeft", "blinkRight",
    "happy", "relaxed", "surprised",
    "aa", "ih", "ou", "ee", "oh",
    "lookUp", "lookDown", "lookLeft", "lookRight",
}

REQUIRED_AURA_CUSTOM_EXPRESSIONS = {
    "auraWarmSmile",
    "auraFocused",
    "auraCompassion",
    "auraConcern",
    "auraCelebrate",
    "auraListening",
    "auraCurious",
    "auraThinking",
    "auraConfident",
}

# Apple ARKit-compatible facial coefficient names. Aura keeps these as face-mesh morph
# target names so future live facial capture can drive the same GLB without remodelling.
ARKIT_FACE_MORPHS = {
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft", "eyeLookUpLeft",
    "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight",
    "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
    "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight", "tongueOut",
}

REQUIRED_ANIMATIONS = {
    "Idle", "Walk", "Talk", "Listen", "Think",
    "PointLeft", "PointRight", "Present", "Celebrate",
}

RECOMMENDED_ANIMATIONS = REQUIRED_ANIMATIONS | {
    "Welcome", "Explain", "Agree", "Acknowledge", "Search", "Create", "Translate", "Goodbye",
}

SEMANTIC_MATERIALS = {
    "skin": ("aura_skin", "skin"),
    "eyes": ("aura_eyes", "eyes", "eye"),
    "hair": ("aura_hair", "hair"),
    "suit": ("aura_suit", "suit", "bodysuit"),
    "heart_core": ("aura_heart_core", "heart", "core"),
    "circuitry": ("aura_circuitry", "circuit", "energy", "emissive"),
}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _name_set(values: list[str] | set[str]) -> set[str]:
    return {_norm(item) for item in values if item}


def _morph_names(gltf: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for mesh in gltf.get("meshes") or []:
        extras = mesh.get("extras") or {}
        for item in extras.get("targetNames") or []:
            if item:
                names.add(str(item))
        for primitive in mesh.get("primitives") or []:
            pextras = primitive.get("extras") or {}
            for item in pextras.get("targetNames") or []:
                if item:
                    names.add(str(item))
    return names


def _triangle_count(gltf: dict[str, Any]) -> int | None:
    accessors = gltf.get("accessors") or []
    total = 0
    found = False
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            mode = int(primitive.get("mode", 4))
            if mode != 4:  # production character meshes should be triangle lists
                continue
            index_accessor = primitive.get("indices")
            if isinstance(index_accessor, int) and 0 <= index_accessor < len(accessors):
                count = int((accessors[index_accessor] or {}).get("count") or 0)
                total += count // 3
                found = True
            else:
                position_accessor = (primitive.get("attributes") or {}).get("POSITION")
                if isinstance(position_accessor, int) and 0 <= position_accessor < len(accessors):
                    count = int((accessors[position_accessor] or {}).get("count") or 0)
                    total += count // 3
                    found = True
    return total if found else None


def _external_uris(gltf: dict[str, Any]) -> list[str]:
    uris: list[str] = []
    for collection in ("buffers", "images"):
        for item in gltf.get(collection) or []:
            uri = str(item.get("uri") or "").strip()
            if uri and not uri.startswith("data:"):
                uris.append(uri)
    return uris


def _material_report(gltf: dict[str, Any]) -> tuple[list[str], list[str], dict[str, bool]]:
    names = [str(item.get("name") or "") for item in (gltf.get("materials") or [])]
    normed = {_norm(name) for name in names}
    missing: list[str] = []
    found: dict[str, bool] = {}
    for semantic, aliases in SEMANTIC_MATERIALS.items():
        matched = any(any(_norm(alias) in material for alias in aliases) for material in normed)
        found[semantic] = matched
        if not matched:
            missing.append(semantic)
    return names, missing, found


def _emissive_semantics(gltf: dict[str, Any]) -> dict[str, bool]:
    result = {"heart_core": False, "circuitry": False}
    for material in gltf.get("materials") or []:
        name = _norm(material.get("name") or "")
        factor = material.get("emissiveFactor") or [0, 0, 0]
        strength = sum(float(x or 0) for x in factor[:3])
        ext = material.get("extensions") or {}
        strength_ext = (ext.get("KHR_materials_emissive_strength") or {}).get("emissiveStrength")
        emissive = strength > 0 or (strength_ext is not None and float(strength_ext) > 0)
        if emissive and ("heart" in name or "core" in name):
            result["heart_core"] = True
        if emissive and ("circuit" in name or "energy" in name or "emissive" in name):
            result["circuitry"] = True
    return result


def validate_aura_production_model(path: Path = MODEL_PATH) -> dict[str, Any]:
    """Strict gate for installing Aura as the production canonical embodied character.

    `validate_aura_model()` answers whether a GLB can be driven by the current VRM runtime.
    This function is intentionally much stricter: it verifies the character contract needed
    for expressive, close-range, mobile delivery and rejects merely-generic rigged humans.
    """
    runtime = validate_aura_model(path)
    result: dict[str, Any] = {
        "runtime": runtime,
        "production_ready": False,
        "blocking_reasons": [],
        "warnings": [],
    }
    if not runtime.get("ready_for_embodied_runtime"):
        result["blocking_reasons"].append(runtime.get("blocking_reason") or "Base VRM runtime validation failed")
        return result

    gltf = _glb_json(path)
    vrm = ((gltf.get("extensions") or {}).get("VRMC_vrm") or {})
    humanoid = (vrm.get("humanoid") or {}).get("humanBones") or {}
    bones = set(humanoid.keys())
    missing_bones = sorted(PRODUCTION_HUMANOID_BONES - bones)

    expressions = vrm.get("expressions") or {}
    preset = set((expressions.get("preset") or {}).keys())
    custom = set((expressions.get("custom") or {}).keys())
    preset_norm = _name_set(preset)
    custom_norm = _name_set(custom)
    missing_preset = sorted(name for name in REQUIRED_VRM_PRESET_EXPRESSIONS if _norm(name) not in preset_norm)
    missing_custom = sorted(name for name in REQUIRED_AURA_CUSTOM_EXPRESSIONS if _norm(name) not in custom_norm)

    morph_names = _morph_names(gltf)
    morph_norm = _name_set(morph_names)
    missing_arkit = sorted(name for name in ARKIT_FACE_MORPHS if _norm(name) not in morph_norm)
    arkit_coverage = round((len(ARKIT_FACE_MORPHS) - len(missing_arkit)) / len(ARKIT_FACE_MORPHS), 3)

    extensions_used = set(gltf.get("extensionsUsed") or []) | set((gltf.get("extensions") or {}).keys())
    spring_bone = (gltf.get("extensions") or {}).get("VRMC_springBone") or {}
    spring_count = len(spring_bone.get("springs") or [])
    has_look_at = bool(vrm.get("lookAt"))
    has_skins = bool(gltf.get("skins"))

    material_names, missing_materials, material_semantics = _material_report(gltf)
    emissive_semantics = _emissive_semantics(gltf)

    animation_names = {str(item.get("name") or "") for item in (gltf.get("animations") or []) if item.get("name")}
    missing_animations = sorted(REQUIRED_ANIMATIONS - animation_names)

    external_uris = _external_uris(gltf)
    triangles = _triangle_count(gltf)
    bytes_total = path.stat().st_size
    max_triangles = int(os.getenv("AURA_AVATAR_MAX_TRIANGLES", "160000"))
    max_bytes = int(os.getenv("AURA_AVATAR_MAX_BYTES", str(40 * 1024 * 1024)))
    mobile_budget_pass = (triangles is None or triangles <= max_triangles) and bytes_total <= max_bytes

    has_ktx2 = "KHR_texture_basisu" in extensions_used or any(
        str(item.get("mimeType") or "").lower() == "image/ktx2" for item in (gltf.get("images") or [])
    )
    has_mesh_compression = bool({"KHR_meshopt_compression", "EXT_meshopt_compression", "KHR_draco_mesh_compression"} & extensions_used)

    result.update(
        {
            "production_humanoid_bone_count": len(bones),
            "missing_production_humanoid_bones": missing_bones,
            "missing_vrm_preset_expressions": missing_preset,
            "missing_aura_custom_expressions": missing_custom,
            "face_morph_target_count": len(morph_names),
            "arkit_face_morph_coverage": arkit_coverage,
            "missing_arkit_face_morphs": missing_arkit,
            "has_vrm_look_at": has_look_at,
            "has_vrm_spring_bone": bool(spring_count),
            "spring_count": spring_count,
            "has_skinning": has_skins,
            "materials": material_names,
            "material_semantics": material_semantics,
            "missing_semantic_materials": missing_materials,
            "emissive_semantics": emissive_semantics,
            "animations": sorted(animation_names),
            "missing_required_animations": missing_animations,
            "recommended_animation_coverage": round(len(animation_names & RECOMMENDED_ANIMATIONS) / len(RECOMMENDED_ANIMATIONS), 3),
            "self_contained_glb": not external_uris,
            "external_uris": external_uris,
            "triangle_count": triangles,
            "max_triangles": max_triangles,
            "bytes": bytes_total,
            "max_bytes": max_bytes,
            "mobile_budget_pass": mobile_budget_pass,
            "ktx2_basis": has_ktx2,
            "mesh_compression": has_mesh_compression,
        }
    )

    blockers: list[str] = result["blocking_reasons"]
    if missing_bones:
        blockers.append("Production humanoid rig is incomplete, including fingers/eyes/toes/shoulders")
    if missing_preset:
        blockers.append("Required VRM preset expressions are incomplete")
    if missing_custom:
        blockers.append("Aura-specific emotional expression set is incomplete")
    if missing_arkit:
        blockers.append("Detailed ARKit-compatible facial morph set is incomplete")
    if not has_look_at:
        blockers.append("VRM LookAt is required for eye contact and interface gaze")
    if not spring_count:
        blockers.append("VRM SpringBone hair/secondary motion is required")
    if not has_skins:
        blockers.append("No glTF skinning data is present")
    if missing_materials:
        blockers.append("Required semantic Aura materials are missing")
    if not emissive_semantics["heart_core"] or not emissive_semantics["circuitry"]:
        blockers.append("Heart core and circuitry must expose emissive materials")
    if missing_animations:
        blockers.append("Required authored motion clips are incomplete")
    if external_uris:
        blockers.append("Production Aura GLB must be self-contained and cannot reference external files")
    if not mobile_budget_pass:
        blockers.append("Aura exceeds the configured mobile geometry/file-size budget")
    if not has_ktx2:
        blockers.append("KTX2/Basis textures are required for production mobile delivery")
    if not has_mesh_compression:
        blockers.append("Mesh compression is required for production mobile delivery")

    result["production_ready"] = not blockers
    return result
