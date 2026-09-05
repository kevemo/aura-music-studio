from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from aura_music_studio.executable_image_design import (
    GradientLayer,
    ImageDesignDocument,
    LayerMask,
    PatternLayer,
    RasterLayer,
    ShapeLayer,
    SolidLayer,
    TextLayer,
    load_image_design_preset,
    render_image_design_document,
    save_image_design_preset,
)
from aura_music_studio.executable_image_effects import ImageEffectGraph, ImageEffectNode


def _asset(path: Path) -> Path:
    image = Image.new("RGBA", (24, 20), (180, 50, 25, 180))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 10, 16), fill=(20, 170, 80, 90))
    draw.ellipse((12, 2, 22, 17), fill=(35, 80, 220, 220))
    image.save(path)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complex_document_renders_editable_layers_and_embedded_effect_graph(tmp_path: Path):
    source = _asset(tmp_path / "portrait.png")
    source_hash = _sha(source)
    destination = tmp_path / "design.png"
    effects = ImageEffectGraph(
        name="Layer look",
        nodes=[
            ImageEffectNode(kind="invert", mix=0.35),
            ImageEffectNode(kind="vignette", amount=1.5, mix=0.5),
        ],
    )
    document = ImageDesignDocument(
        name="Launch artwork",
        width=128,
        height=96,
        background="#0C1020FF",
        layers=[
            GradientLayer(
                id="gradient",
                width=128,
                height=96,
                start_color="#24103FFF",
                end_color="#205D78FF",
                direction="horizontal",
            ),
            PatternLayer(
                id="stars",
                width=128,
                height=96,
                pattern="dots",
                foreground="#FFFFFF55",
                background="#00000000",
                cell_size=16,
                opacity=0.45,
            ),
            RasterLayer(
                id="portrait",
                asset_id="portrait-main",
                x=10,
                y=12,
                width=42,
                height=36,
                effects=effects,
                mask=LayerMask(kind="ellipse", width=42, height=36, feather=1.0),
            ),
            ShapeLayer(
                id="portrait-frame",
                x=8,
                y=10,
                width=46,
                height=40,
                fill="#00000000",
                outline="#F5D98AFF",
                stroke_width=2,
            ),
            TextLayer(
                id="title",
                x=58,
                y=18,
                width=62,
                text="Aura Image Designer",
                font_size=14,
                color="#FFFFFFFF",
            ),
        ],
    )

    evidence = render_image_design_document(
        document,
        destination,
        assets={"portrait-main": source},
    )

    assert destination.is_file()
    assert evidence["rendered"] is True
    assert evidence["rendered_layer_ids"] == [
        "gradient",
        "stars",
        "portrait",
        "portrait-frame",
        "title",
    ]
    assert evidence["asset_digests"] == {"portrait-main": source_hash}
    assert evidence["effect_graph_fingerprints"] == {"portrait": effects.fingerprint()}
    assert evidence["asset_reference_policy"] == "server_bound_ids_only"
    assert evidence["arbitrary_code_execution"] is False
    assert evidence["network_access"] is False
    assert _sha(source) == source_hash
    with Image.open(destination) as rendered:
        assert rendered.size == (128, 96)
        assert rendered.convert("RGBA").getbbox() == (0, 0, 128, 96)


def test_document_serialization_contains_asset_id_not_server_path(tmp_path: Path):
    source = _asset(tmp_path / "private-project-source.png")
    document = ImageDesignDocument(
        width=64,
        height=64,
        layers=[RasterLayer(id="photo", asset_id="project-photo")],
    )

    serialized = document.model_dump_json()

    assert "project-photo" in serialized
    assert str(source) not in serialized
    with pytest.raises(KeyError):
        render_image_design_document(document, tmp_path / "missing.png", assets={})


def test_ellipse_mask_and_transparent_canvas_preserve_editability(tmp_path: Path):
    document = ImageDesignDocument(
        width=64,
        height=64,
        background="#00000000",
        layers=[
            SolidLayer(
                id="masked",
                x=12,
                y=12,
                width=40,
                height=40,
                color="#E43D52FF",
                mask=LayerMask(kind="ellipse", width=40, height=40),
            )
        ],
    )
    destination = tmp_path / "masked.png"

    render_image_design_document(document, destination)

    with Image.open(destination) as rendered:
        rgba = rendered.convert("RGBA")
        assert rgba.getpixel((12, 12))[3] == 0
        assert rgba.getpixel((32, 32))[3] == 255
        assert rgba.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("pattern", ["checker", "stripes", "dots"])
def test_pattern_primitives_render_real_pixels(tmp_path: Path, pattern: str):
    document = ImageDesignDocument(
        width=64,
        height=64,
        background="#111111FF",
        layers=[
            PatternLayer(
                id="pattern",
                width=64,
                height=64,
                pattern=pattern,
                foreground="#FFFFFFFF",
                background="#00000000",
                cell_size=16,
            )
        ],
    )
    destination = tmp_path / f"{pattern}.png"

    render_image_design_document(document, destination)

    with Image.open(destination) as rendered:
        colors = set(rendered.convert("RGBA").getdata())
        assert len(colors) > 1


def test_horizontal_gradient_reaches_both_authored_end_colours(tmp_path: Path):
    document = ImageDesignDocument(
        width=64,
        height=64,
        background="#00000000",
        layers=[
            GradientLayer(
                id="gradient",
                width=64,
                height=64,
                start_color="#FF0000FF",
                end_color="#0000FFFF",
            )
        ],
    )
    destination = tmp_path / "gradient.png"

    render_image_design_document(document, destination)

    with Image.open(destination) as rendered:
        rgba = rendered.convert("RGBA")
        assert rgba.getpixel((0, 32)) == (255, 0, 0, 255)
        assert rgba.getpixel((63, 32)) == (0, 0, 255, 255)


@pytest.mark.parametrize(
    ("blend", "comparison"),
    [
        ("multiply", "darker"),
        ("screen", "lighter"),
    ],
)
def test_bounded_blend_modes_execute_against_canvas(
    tmp_path: Path,
    blend: str,
    comparison: str,
):
    document = ImageDesignDocument(
        width=64,
        height=64,
        background="#646464FF",
        layers=[
            SolidLayer(
                id="blend",
                width=64,
                height=64,
                color="#C8C8C8FF",
                blend=blend,
            )
        ],
    )
    destination = tmp_path / f"{blend}.png"

    render_image_design_document(document, destination)

    with Image.open(destination) as rendered:
        channel = rendered.convert("RGBA").getpixel((32, 32))[0]
        if comparison == "darker":
            assert channel < 100
        else:
            assert channel > 200


def test_text_layer_uses_bounded_internal_font_and_draws_pixels(tmp_path: Path):
    document = ImageDesignDocument(
        width=160,
        height=80,
        background="#00000000",
        layers=[
            TextLayer(
                id="title",
                x=4,
                y=4,
                width=150,
                text="Elevate Your Soul Through Purposeful Media",
                font_size=18,
                color="#F5D98AFF",
                align="center",
            )
        ],
    )
    destination = tmp_path / "text.png"

    evidence = render_image_design_document(document, destination)

    assert evidence["typography_font_policy"] == "pillow_default_aura_sans_only"
    with Image.open(destination) as rendered:
        assert rendered.convert("RGBA").getbbox() is not None
    with pytest.raises(ValidationError):
        TextLayer(
            id="unsafe-font",
            text="No arbitrary fonts",
            font_family="../../private.ttf",
        )


def test_invisible_and_zero_opacity_layers_are_skipped_deterministically(tmp_path: Path):
    document = ImageDesignDocument(
        width=64,
        height=64,
        layers=[
            SolidLayer(id="visible", width=10, height=10, color="#FFFFFFFF"),
            SolidLayer(id="hidden", width=10, height=10, visible=False),
            SolidLayer(id="zero", width=10, height=10, opacity=0.0),
        ],
    )

    evidence = render_image_design_document(document, tmp_path / "skip.png")

    assert evidence["rendered_layer_ids"] == ["visible"]
    assert evidence["skipped_layer_ids"] == ["hidden", "zero"]


def test_design_preset_roundtrip_preserves_nested_effect_graph_and_fingerprint(tmp_path: Path):
    document = ImageDesignDocument(
        name="Reusable campaign card",
        width=1080,
        height=1080,
        layers=[
            RasterLayer(
                id="photo",
                asset_id="photo-1",
                effects=ImageEffectGraph(
                    nodes=[ImageEffectNode(kind="duotone", mix=0.6)]
                ),
            ),
            TextLayer(id="caption", text="Reusable editable title"),
        ],
    )
    library = tmp_path / "design-library"

    target = save_image_design_preset(library, "campaign-card", document)
    loaded = load_image_design_preset(library, "campaign-card")

    assert target.is_file()
    assert loaded == document
    assert loaded.fingerprint() == document.fingerprint()
    with pytest.raises(ValueError):
        save_image_design_preset(library, "../escape", document)
    with pytest.raises(ValueError):
        load_image_design_preset(library, "nested/design")


def test_document_and_resource_bounds_fail_closed():
    with pytest.raises(ValidationError):
        ImageDesignDocument(
            width=64,
            height=64,
            layers=[SolidLayer(id="duplicate", width=8, height=8), SolidLayer(id="duplicate", width=8, height=8)],
        )
    with pytest.raises(ValidationError):
        RasterLayer(id="photo", asset_id="../private")
    with pytest.raises(ValidationError):
        RasterLayer(id="photo", asset_id="photo", width=100, height=None)
    with pytest.raises(ValidationError):
        PatternLayer(
            id="dense",
            width=4096,
            height=4096,
            cell_size=8,
        )
    with pytest.raises(ValidationError):
        ImageDesignDocument(
            width=64,
            height=64,
            layers=[SolidLayer(id=f"layer-{index}", width=1, height=1) for index in range(65)],
        )


def test_unsupported_output_format_fails_closed(tmp_path: Path):
    document = ImageDesignDocument(width=64, height=64)

    with pytest.raises(ValueError):
        render_image_design_document(document, tmp_path / "design.psd")
