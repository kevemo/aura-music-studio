import json
import struct
from pathlib import Path

from aura_music_studio.aura_avatar_energy_contract import validate_aura_live_energy_materials


def _write_glb(path: Path, materials: list[dict]) -> None:
    document = {"asset": {"version": "2.0"}, "materials": materials}
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((-len(raw)) % 4)
    total = 12 + 8 + len(raw)
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, total))
        handle.write(struct.pack("<II", len(raw), 0x4E4F534A))
        handle.write(raw)


def test_all_three_live_energy_channels_are_required(tmp_path: Path):
    model = tmp_path / "aura.glb"
    _write_glb(
        model,
        [
            {"name": "Aura_Eyes", "emissiveFactor": [1.0, 0.1, 0.8]},
            {"name": "Aura_Heart_Core", "emissiveFactor": [1.0, 0.05, 0.4]},
            {"name": "Aura_Circuitry", "emissiveFactor": [0.4, 0.05, 1.0]},
        ],
    )
    result = validate_aura_live_energy_materials(model)
    assert result["ready"] is True
    assert result["blocking_reasons"] == []
    assert result["channels"]["eyes"]["emissive"] is True
    assert result["channels"]["heart_core"]["emissive"] is True
    assert result["channels"]["circuitry"]["emissive"] is True


def test_non_emissive_eyes_block_aura_installation(tmp_path: Path):
    model = tmp_path / "aura.glb"
    _write_glb(
        model,
        [
            {"name": "Aura_Eyes"},
            {"name": "Aura_Heart_Core", "emissiveFactor": [1.0, 0.05, 0.4]},
            {"name": "Aura_Circuitry", "emissiveFactor": [0.4, 0.05, 1.0]},
        ],
    )
    result = validate_aura_live_energy_materials(model)
    assert result["ready"] is False
    assert "Aura eyes material is not emissive" in result["blocking_reasons"]


def test_missing_channel_is_rejected_without_guessing(tmp_path: Path):
    model = tmp_path / "aura.glb"
    _write_glb(
        model,
        [
            {"name": "Aura_Eyes", "emissiveFactor": [1.0, 0.1, 0.8]},
            {"name": "Aura_Heart_Core", "emissiveFactor": [1.0, 0.05, 0.4]},
        ],
    )
    result = validate_aura_live_energy_materials(model)
    assert result["ready"] is False
    assert "Aura circuitry material is missing" in result["blocking_reasons"]
