from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from aura_music_studio.aura_image_effect_system import (
    compose_image_effect_system,
    load_reusable_image_effect_system,
    preview_image_effect_system,
    save_reusable_image_effect_system,
)


def _source(path: Path) -> Path:
    image = Image.new("RGBA", (20, 12), (90, 120, 160, 140))
    image.putpixel((2, 2), (230, 30, 20, 70))
    image.save(path)
    return path


def test_prompt_composes_bounded_executable_editable_graph():
    result = compose_image_effect_system(
        "brighten slightly, increase contrast, then gaussian blur 1.5"
    )

    assert result["backend_executable"] is True
    assert result["editable_graph"] is True
    assert result["runtime"] == "local_allowlisted_pillow"
    assert result["arbitrary_code_execution"] is False
    assert result["network_access"] is False
    assert result["project_mutated"] is False
    assert [node["kind"] for node in result["graph"]["nodes"]] == [
        "brightness",
        "contrast",
        "gaussian_blur",
    ]
    assert len(result["fingerprint"]) == 64
    assert len(result["prompt_fingerprint"]) == 64


def test_plain_and_composes_each_effect_without_breaking_grayscale_alias():
    result = compose_image_effect_system("increase brightness and contrast and black and white")
    assert [node["kind"] for node in result["graph"]["nodes"]] == [
        "brightness",
        "contrast",
        "grayscale",
    ]


def test_unsupported_prompt_instruction_fails_closed():
    with pytest.raises(ValueError, match="Unsupported image effect instruction"):
        compose_image_effect_system("brighten the portrait, then run my custom plugin")


def test_prompt_cannot_create_shell_network_or_arbitrary_runtime_nodes():
    for prompt in (
        "curl https://example.com",
        "execute shell command",
        "load remote plugin",
        "run python code",
    ):
        with pytest.raises(ValueError):
            compose_image_effect_system(prompt)


def test_preview_executes_real_pixels_preserves_source_and_returns_exact_token(tmp_path: Path):
    source = _source(tmp_path / "source.png")
    before = source.read_bytes()
    composed = compose_image_effect_system("increase brightness and contrast")
    assert [node["kind"] for node in composed["graph"]["nodes"]] == ["brightness", "contrast"]
    preview = tmp_path / "preview.png"

    result = preview_image_effect_system(source, preview, composed["graph"])

    assert result["preview"] is True
    assert result["preview_token"] == composed["fingerprint"]
    assert result["source_media_mutated"] is False
    assert result["arbitrary_code_execution"] is False
    assert result["network_access"] is False
    assert source.read_bytes() == before
    assert preview.is_file()
    with Image.open(source) as original, Image.open(preview) as rendered:
        assert rendered.convert("RGBA").tobytes() != original.convert("RGBA").tobytes()
        assert rendered.convert("RGBA").getchannel("A").tobytes() == original.convert("RGBA").getchannel("A").tobytes()


def test_private_reusable_save_requires_exact_preview_fingerprint(tmp_path: Path):
    composed = compose_image_effect_system("invert")
    presets = tmp_path / "private-presets"

    saved = save_reusable_image_effect_system(
        presets,
        "portrait-negative",
        composed["graph"],
        expected_fingerprint=composed["fingerprint"],
    )

    assert saved["saved"] is True
    assert saved["fingerprint"] == composed["fingerprint"]
    assert saved["private_reusable_preset"] is True
    assert saved["marketplace_published"] is False
    assert saved["sale_enabled"] is False
    assert saved["path_exposed"] is False

    loaded = load_reusable_image_effect_system(presets, "portrait-negative")
    assert loaded["fingerprint"] == composed["fingerprint"]
    assert loaded["graph"] == composed["graph"]
    assert loaded["marketplace_published"] is False
    assert loaded["sale_enabled"] is False


def test_changed_graph_is_rejected_after_preview_before_save(tmp_path: Path):
    composed = compose_image_effect_system("invert")
    changed = composed["graph"]
    changed["nodes"].append(
        {
            "kind": "grayscale",
            "enabled": True,
            "mix": 1.0,
            "amount": 1.0,
            "radius": 2.0,
            "bits": 6,
        }
    )

    with pytest.raises(RuntimeError, match="changed after preview"):
        save_reusable_image_effect_system(
            tmp_path / "private-presets",
            "changed",
            changed,
            expected_fingerprint=composed["fingerprint"],
        )


def test_reusable_preset_name_remains_path_confined(tmp_path: Path):
    composed = compose_image_effect_system("grayscale")
    for name in ("../escape", "nested/name", "/absolute"):
        with pytest.raises(ValueError):
            save_reusable_image_effect_system(
                tmp_path / "private-presets",
                name,
                composed["graph"],
                expected_fingerprint=composed["fingerprint"],
            )


def test_prompt_node_count_is_bounded():
    prompt = ", ".join(["invert"] * 17)
    with pytest.raises(ValueError, match="exceeds 16 executable nodes"):
        compose_image_effect_system(prompt)
