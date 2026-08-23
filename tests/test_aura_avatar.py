import json
import struct
from pathlib import Path

from aura_music_studio.aura_avatar import REQUIRED_HUMANOID_BONES, validate_aura_model
from aura_music_studio.aura_avatar_bootstrap import PATCHED_RUNTIME_JS, avatar_bootstrap_html


def _write_glb(path: Path, document: dict) -> None:
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    padding = (-len(raw)) % 4
    raw += b" " * padding
    total = 12 + 8 + len(raw)
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, total))
        handle.write(struct.pack("<II", len(raw), 0x4E4F534A))
        handle.write(raw)


def test_missing_model_never_claims_generic_avatar(tmp_path: Path):
    result = validate_aura_model(tmp_path / "missing.glb")
    assert result["exists"] is False
    assert result["valid_glb"] is False
    assert "generic avatar will not be substituted" in result["blocking_reason"].lower()


def test_valid_vrm1_humanoid_passes_structural_gate(tmp_path: Path):
    bones = {name: {"node": index} for index, name in enumerate(sorted(REQUIRED_HUMANOID_BONES))}
    nodes = [{"name": name} for name in sorted(REQUIRED_HUMANOID_BONES)]
    document = {
        "asset": {"version": "2.0", "generator": "Aura test"},
        "nodes": nodes,
        "extensionsUsed": ["VRMC_vrm"],
        "extensions": {
            "VRMC_vrm": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": bones},
                "expressions": {
                    "preset": {
                        "blink": {}, "aa": {}, "ih": {}, "ou": {}, "ee": {}, "oh": {},
                        "happy": {}, "relaxed": {}, "surprised": {},
                        "lookUp": {}, "lookDown": {}, "lookLeft": {}, "lookRight": {},
                    }
                },
                "meta": {"name": "Aura"},
            }
        },
        "animations": [{"name": "Idle"}, {"name": "Walk"}, {"name": "Talk"}],
    }
    model = tmp_path / "aura.glb"
    _write_glb(model, document)
    result = validate_aura_model(model)
    assert result["valid_glb"] is True
    assert result["vrm_1"] is True
    assert result["missing_humanoid_bones"] == []
    assert result["recommended_expression_coverage"] == 1.0
    assert result["ready_for_embodied_runtime"] is True


def test_plain_glb_without_vrm_is_rejected(tmp_path: Path):
    model = tmp_path / "plain.glb"
    _write_glb(model, {"asset": {"version": "2.0"}})
    result = validate_aura_model(model)
    assert result["valid_glb"] is True
    assert result["vrm_1"] is False
    assert result["ready_for_embodied_runtime"] is False


def test_browser_bootstrap_is_pinned_and_runtime_typo_is_patched():
    html = avatar_bootstrap_html()
    assert "three@0.180.0" in html
    assert "three-vrm@3.5.5" in html
    assert "runtime-v3.js" in html
    assert "this.state==='thinking'?.55:0" not in PATCHED_RUNTIME_JS
    assert "(this.state==='thinking'?0.55:0)" in PATCHED_RUNTIME_JS
    assert "page-audio" in PATCHED_RUNTIME_JS
