import json
import struct

import pytest

from aura_music_studio.aura_avatar_validator import validate_aura_glb


def _write_glb(path, document):
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total = 12 + 8 + len(raw)
    path.write_bytes(
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(raw), 0x4E4F534A)
        + raw
    )


def _document(**extra):
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": []}],
    }
    document.update(extra)
    return document


def test_self_contained_glb_without_uri_is_import_safe(tmp_path):
    model = tmp_path / "rhian.glb"
    _write_glb(model, _document(buffers=[{"byteLength": 0}]))

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is True
    assert validation["import_safe"] is True
    assert validation["import_policy"] == "self_contained_glb_v1"
    assert validation["resource_uri_count"] == 0
    assert validation["external_resource_count"] == 0
    assert validation["blocked_resource_count"] == 0


def test_allowlisted_embedded_png_data_uri_is_import_safe(tmp_path):
    model = tmp_path / "rhian.glb"
    # A tiny valid PNG payload; embedded raster data is inert and remains inside the GLB JSON.
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    _write_glb(model, _document(images=[{"uri": f"data:image/png;base64,{png}"}]))

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is True
    assert validation["import_safe"] is True
    assert validation["embedded_image_data_uri_count"] == 1
    assert validation["external_resource_count"] == 0


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.invalid/avatar.png",
        "textures/avatar.png",
        "file:///tmp/avatar.png",
        "javascript:alert(1)",
        "//example.invalid/avatar.png",
    ],
)
def test_external_or_active_image_uri_is_blocked(tmp_path, uri):
    model = tmp_path / "rhian.glb"
    _write_glb(model, _document(images=[{"uri": uri}]))

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is False
    assert validation["import_safe"] is False
    assert validation["external_resource_count"] == 1
    assert validation["blocked_resource_count"] == 1
    assert "Unsafe GLB resource references" in validation["error"]


@pytest.mark.parametrize(
    "uri",
    [
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:text/html;base64,PGh0bWw+PC9odG1sPg==",
        "data:image/png;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "data:image/png,not-base64",
    ],
)
def test_non_allowlisted_or_mislabeled_embedded_image_is_blocked(tmp_path, uri):
    model = tmp_path / "rhian.glb"
    _write_glb(model, _document(images=[{"uri": uri}]))

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is False
    assert validation["import_safe"] is False
    assert validation["blocked_resource_count"] == 1


def test_buffer_uri_is_blocked_even_when_it_is_a_data_uri(tmp_path):
    model = tmp_path / "rhian.glb"
    _write_glb(
        model,
        _document(buffers=[{"byteLength": 1, "uri": "data:application/octet-stream;base64,AA=="}]),
    )

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is False
    assert validation["import_safe"] is False
    assert validation["external_resource_count"] == 1
    assert "buffers.0.uri is not permitted" in validation["error"]


def test_uri_hidden_in_extension_fails_closed(tmp_path):
    model = tmp_path / "rhian.glb"
    _write_glb(
        model,
        _document(
            extensions={
                "VENDOR_custom": {
                    "uri": "https://example.invalid/runtime-resource.bin",
                }
            }
        ),
    )

    validation = validate_aura_glb(model)

    assert validation["valid_glb"] is False
    assert validation["import_safe"] is False
    assert validation["external_resource_count"] == 1
    assert "unsupported URI-bearing field" in validation["error"]
