from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.executable_image_design import (
    ImageDesignDocument,
    TextLayer,
    render_image_design_document,
)


def test_extreme_wrapped_text_fails_before_oversized_layer_allocation(tmp_path: Path):
    document = ImageDesignDocument(
        width=64,
        height=64,
        layers=[
            TextLayer(
                id="bounded-copy",
                width=32,
                text=" ".join("a" for _ in range(900)),
                font_size=64,
                padding=2,
            )
        ],
    )

    with pytest.raises(ValueError, match="Rendered text layer exceeds"):
        render_image_design_document(document, tmp_path / "bounded.png")


def test_normal_wrapped_text_remains_renderable(tmp_path: Path):
    document = ImageDesignDocument(
        width=180,
        height=100,
        layers=[
            TextLayer(
                id="normal-copy",
                width=170,
                text="Create, edit and keep every layer reusable.",
                font_size=16,
                padding=4,
            )
        ],
    )

    evidence = render_image_design_document(document, tmp_path / "normal.png")

    assert evidence["rendered_layer_ids"] == ["normal-copy"]
