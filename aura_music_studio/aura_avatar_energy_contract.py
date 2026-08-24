from __future__ import annotations

from pathlib import Path
from typing import Any

from .aura_avatar import MODEL_PATH, _glb_json

ENERGY_CHANNEL_ALIASES = {
    "eyes": ("aura_eyes", "eye_glow", "iris"),
    "heart_core": ("aura_heart_core", "heart", "core"),
    "circuitry": ("aura_circuitry", "circuit", "energy"),
}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _is_emissive(material: dict[str, Any]) -> bool:
    factor = material.get("emissiveFactor") or [0, 0, 0]
    try:
        if sum(float(value or 0) for value in factor[:3]) > 0:
            return True
    except Exception:
        pass
    extension = ((material.get("extensions") or {}).get("KHR_materials_emissive_strength") or {})
    try:
        return float(extension.get("emissiveStrength") or 0) > 0
    except Exception:
        return False


def validate_aura_live_energy_materials(path: Path = MODEL_PATH) -> dict[str, Any]:
    """Verify that Aura's three communicating light systems can be driven independently."""
    if not path.is_file():
        return {
            "ready": False,
            "channels": {name: {"found": False, "emissive": False} for name in ENERGY_CHANNEL_ALIASES},
            "blocking_reasons": ["Aura model is missing"],
        }

    gltf = _glb_json(path)
    materials = gltf.get("materials") or []
    report: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for channel, aliases in ENERGY_CHANNEL_ALIASES.items():
        matches = []
        for material in materials:
            name = str(material.get("name") or "")
            normalized = _norm(name)
            if any(_norm(alias) in normalized for alias in aliases):
                matches.append({"name": name, "emissive": _is_emissive(material)})
        found = bool(matches)
        emissive = any(item["emissive"] for item in matches)
        report[channel] = {"found": found, "emissive": emissive, "materials": matches}
        if not found:
            blockers.append(f"Aura {channel} material is missing")
        elif not emissive:
            blockers.append(f"Aura {channel} material is not emissive")

    return {
        "ready": not blockers,
        "channels": report,
        "blocking_reasons": blockers,
    }
