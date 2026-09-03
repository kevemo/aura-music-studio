from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from aura_music_studio.executable_image_effects import (
    ImageEffectGraph,
    ImageEffectNode,
    load_image_effect_preset,
    render_image_effect_graph,
    save_image_effect_preset,
)


def _source(path: Path) -> Path:
    image = Image.new("RGBA", (16, 12), (80, 120, 160, 127))
    image.putpixel((0, 0), (240, 20, 10, 64))
    image.save(path)
    return path


def test_real_pixel_effect_graph_modifies_image_and_preserves_alpha(tmp_path: Path):
    source = _source(tmp_path / "source.png")
    destination = tmp_path / "render.png"
    graph = ImageEffectGraph(
        name="Portrait polish",
        nodes=[
            ImageEffectNode(kind="brightness", amount=1.5),
            ImageEffectNode(kind="contrast", amount=1.2),
            ImageEffectNode(kind="saturation", amount=0.5),
        ],
    )

    evidence = render_image_effect_graph(source, destination, graph)

    assert evidence["rendered"] is True
    assert evidence["node_count"] == 3
    assert evidence["image_origin"] == "local_allowlisted_pixel_effects"
    assert evidence["arbitrary_code_execution"] is False
    assert evidence["network_access"] is False
    with Image.open(source) as before, Image.open(destination) as after:
        assert after.convert("RGBA").tobytes() != before.convert("RGBA").tobytes()
        assert after.convert("RGBA").getchannel("A").tobytes() == before.convert("RGBA").getchannel("A").tobytes()


def test_mix_zero_is_non_destructive(tmp_path: Path):
    source = _source(tmp_path / "source.png")
    destination = tmp_path / "render.png"
    graph = ImageEffectGraph(nodes=[ImageEffectNode(kind="invert", mix=0.0)])

    render_image_effect_graph(source, destination, graph)

    with Image.open(source) as before, Image.open(destination) as after:
        assert after.convert("RGBA").tobytes() == before.convert("RGBA").tobytes()


def test_grayscale_and_posterize_execute_with_alpha(tmp_path: Path):
    source = _source(tmp_path / "source.png")
    destination = tmp_path / "render.webp"
    graph = ImageEffectGraph(
        nodes=[
            ImageEffectNode(kind="grayscale"),
            ImageEffectNode(kind="posterize", bits=4),
            ImageEffectNode(kind="gaussian_blur", radius=0.5, mix=0.5),
        ]
    )

    evidence = render_image_effect_graph(source, destination, graph)

    assert evidence["width"] == 16
    assert evidence["height"] == 12
    with Image.open(destination) as rendered:
        assert rendered.size == (16, 12)


def test_unknown_effect_kind_fails_closed():
    with pytest.raises(ValidationError):
        ImageEffectNode(kind="run_plugin")


def test_graph_node_count_is_bounded():
    nodes = [ImageEffectNode(kind="brightness") for _ in range(17)]
    with pytest.raises(ValidationError):
        ImageEffectGraph(nodes=nodes)


def test_effect_parameters_are_bounded():
    with pytest.raises(ValidationError):
        ImageEffectNode(kind="gaussian_blur", radius=500.0)
    with pytest.raises(ValidationError):
        ImageEffectNode(kind="posterize", bits=0)
    with pytest.raises(ValidationError):
        ImageEffectNode(kind="brightness", amount=10.0)


def test_preset_round_trip_is_editable_and_deterministic(tmp_path: Path):
    graph = ImageEffectGraph(
        name="Reusable Look",
        nodes=[ImageEffectNode(kind="sharpness", amount=1.75, mix=0.6)],
    )
    presets = tmp_path / "presets"

    target = save_image_effect_preset(presets, "reusable-look", graph)
    loaded = load_image_effect_preset(presets, "reusable-look")

    assert target.is_file()
    assert loaded == graph
    assert loaded.fingerprint() == graph.fingerprint()
    loaded.nodes[0].amount = 1.25
    assert loaded.fingerprint() != graph.fingerprint()


def test_preset_names_reject_path_traversal(tmp_path: Path):
    graph = ImageEffectGraph(nodes=[ImageEffectNode(kind="invert")])

    with pytest.raises(ValueError):
        save_image_effect_preset(tmp_path, "../escape", graph)
    with pytest.raises(ValueError):
        save_image_effect_preset(tmp_path, "nested/name", graph)
    with pytest.raises(ValueError):
        load_image_effect_preset(tmp_path, "../escape")


def test_unsupported_input_and_output_formats_fail_closed(tmp_path: Path):
    source = tmp_path / "source.bmp"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(source)
    graph = ImageEffectGraph(nodes=[])

    with pytest.raises(ValueError):
        render_image_effect_graph(source, tmp_path / "render.png", graph)

    png_source = _source(tmp_path / "source.png")
    with pytest.raises(ValueError):
        render_image_effect_graph(png_source, tmp_path / "render.tiff", graph)
