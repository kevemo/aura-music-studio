from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from aura_music_studio.legacy_reference_index import build_legacy_reference_index, scan_zip


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            archive.writestr(name, payload)
    return stream.getvalue()


def test_scan_zip_indexes_source_urls_dependencies_and_subsystems(tmp_path: Path):
    source = tmp_path / "legacy.zip"
    source.write_bytes(
        _zip_bytes(
            {
                "AuraCore/auraos/aura.os.scheduler.js": (
                    "import helper from './helper.js';\n"
                    "const docs = 'https://example.test/docs';\n"
                    "export function schedule() { return helper(); }\n"
                ),
                "AuraCore/frontend/public/avatar/Aura-3d.glb": b"glTF-binary-placeholder",
                "AuraCore/game engines/WorldForge/network/server.js": "export const multiplayer = true;",
            }
        )
    )

    index = scan_zip(source)
    rows = {row.path: row for row in index.files}

    scheduler = rows["AuraCore/auraos/aura.os.scheduler.js"]
    assert scheduler.language == "JavaScript"
    assert scheduler.readable_text is True
    assert "aura_os" in scheduler.likely_subsystems
    assert "orchestration" in scheduler.likely_subsystems
    assert scheduler.urls == ("https://example.test/docs",)
    assert "./helper.js" in scheduler.dependency_hints
    assert scheduler.owner_source_candidate is True

    avatar = rows["AuraCore/frontend/public/avatar/Aura-3d.glb"]
    assert "avatar_3d" in avatar.likely_subsystems
    assert avatar.readable_text is False
    assert avatar.owner_source_candidate is False

    game = rows["AuraCore/game engines/WorldForge/network/server.js"]
    assert "game_forge" in game.likely_subsystems
    assert "multiplayer" in game.likely_subsystems


def test_sensitive_material_is_flagged_without_exposing_contents(tmp_path: Path):
    source = tmp_path / "legacy.zip"
    source.write_bytes(
        _zip_bytes(
            {
                "AuraCore/.env": "API_TOKEN=do-not-copy",
                "AuraCore/backend/config/drive-service-key.json": '{"private_key":"do-not-copy"}',
            }
        )
    )

    index = scan_zip(source)
    rows = {row.path: row for row in index.files}
    assert rows["AuraCore/.env"].security_sensitive is True
    assert rows["AuraCore/backend/config/drive-service-key.json"].security_sensitive is True
    assert rows["AuraCore/.env"].owner_source_candidate is False

    public = index.public()
    encoded = json.dumps(public)
    assert "do-not-copy" not in encoded
    assert "API_TOKEN" not in encoded


def test_nested_zip_is_recursively_indexed(tmp_path: Path):
    nested = _zip_bytes({"modules/runtime/world.delta.js": "export const delta = {};"})
    source = tmp_path / "legacy.zip"
    source.write_bytes(_zip_bytes({"backend/modules.zip": nested}))

    index = scan_zip(source)
    assert index.nested_archives_scanned == ("legacy.zip!backend/modules.zip",)
    paths = {row.path for row in index.files}
    assert "backend/modules.zip" in paths
    assert "modules/runtime/world.delta.js" in paths


def test_generated_vendor_and_licence_evidence_do_not_inflate_owner_source(tmp_path: Path):
    source = tmp_path / "legacy.zip"
    source.write_bytes(
        _zip_bytes(
            {
                "src/runtime/world.js": "export const world = true;",
                "dist/runtime/world.js": "export const built = true;",
                "node_modules/pkg/index.js": "module.exports = {};",
                ".git/config": "[core]",
                "LICENSE": "Example licence text",
            }
        )
    )

    index = scan_zip(source)
    rows = {row.path: row for row in index.files}
    assert rows["src/runtime/world.js"].owner_source_candidate is True
    assert rows["dist/runtime/world.js"].generated_or_vendor is True
    assert rows["node_modules/pkg/index.js"].generated_or_vendor is True
    assert rows[".git/config"].generated_or_vendor is True
    assert rows["LICENSE"].licence_evidence is True
    assert rows["LICENSE"].owner_source_candidate is False

    public = index.public()
    assert public["owner_source_candidate_count"] == 1
    assert public["generated_or_vendor_count"] == 3
    assert public["licence_evidence_count"] == 1


def test_build_reference_index_is_metadata_first_and_counts_files(tmp_path: Path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first.write_bytes(_zip_bytes({"one.js": "export const one = 1;"}))
    second.write_bytes(_zip_bytes({"two.py": "value = 2"}))

    payload = build_legacy_reference_index([first, second])
    assert payload["schema_version"] == 2
    assert payload["classification_default"] == "UNCLEAR_PROVENANCE"
    assert payload["archive_count"] == 2
    assert payload["file_count"] == 2
    assert payload["owner_source_candidate_count"] == 2
    assert "must not be copied" in payload["security_rule"]
    assert "Generated output" in payload["owner_source_rule"]
