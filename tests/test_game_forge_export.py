import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

import aura_music_studio.game_forge_export as exports
from aura_music_studio.game_forge_models import GameBuild, GameDNA


def _game(content_hash: str = "a" * 64) -> GameDNA:
    game = GameDNA(
        id="game_exporttest",
        title="Export Test",
        prompt="original game",
        dimension="2d",
        engine_target="aura2d",
        rights_confirmed=True,
        rights_attestation="I own or have permission to use the project material.",
    )
    game.latest_build = GameBuild(
        content_hash=content_hash,
        requested_engine="aura2d",
        runtime="aura_game_runtime_2d_canvas_v6_state_machine",
    )
    return game


def _fake_webp(width: int = 384, height: int = 384) -> bytes:
    payload = bytes([0, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = b"VP8X" + len(payload).to_bytes(4, "little") + payload
    body = b"WEBP" + chunk
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_external_adapters_are_truthfully_not_production_ready():
    caps = exports.export_capabilities()
    assert caps["production_ready_targets"] == ["aura_web"]
    assert caps["external_adapters_claimed_ready"] is False
    assert caps["targets"]["aura_web"]["installable_pwa"] is True
    assert caps["targets"]["aura_web"]["offline_core"] is True
    assert caps["targets"]["aura_web"]["format"] == "deterministic_pwa_zip_v2"
    for target in ("phaser4", "playcanvas", "babylon", "godot"):
        assert caps["targets"][target]["production_ready"] is False
        assert caps["targets"][target]["executable_export"] is False
        with pytest.raises(ValueError, match="planned"):
            exports._validate_exportable(_game(), target)


def test_export_requires_rights_and_current_integrity_bound_build(monkeypatch):
    content_hash = "b" * 64
    game = _game(content_hash)
    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(exports, "asset_publication_blockers", lambda _game_id: [])
    assert exports._validate_exportable(game, "aura_web") == content_hash

    game.rights_confirmed = False
    with pytest.raises(ValueError, match="rights"):
        exports._validate_exportable(game, "aura_web")

    game.rights_confirmed = True
    game.rights_attestation = "rights confirmed"
    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: "c" * 64)
    with pytest.raises(ValueError, match="stale"):
        exports._validate_exportable(game, "aura_web")


def test_asset_publication_blockers_fail_closed(monkeypatch):
    game = _game()
    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: game.latest_build.content_hash)
    monkeypatch.setattr(exports, "asset_publication_blockers", lambda _game_id: ["Asset rights missing"])
    with pytest.raises(ValueError, match="Asset rights missing"):
        exports._validate_exportable(game, "aura_web")


def test_archive_path_guard_rejects_traversal():
    from io import BytesIO

    with ZipFile(BytesIO(), "w") as zf:
        with pytest.raises(ValueError, match="Unsafe export archive path"):
            exports._write_zip_entry(zf, "../secret.txt", b"no")


def test_webp_dimensions_are_derived_from_packaged_artwork():
    assert exports._webp_size(_fake_webp(512, 384)) == "512x384"
    assert exports._webp_size(b"not-webp") is None


def test_same_build_and_media_produce_identical_installable_export_bytes(tmp_path: Path, monkeypatch):
    content_hash = "d" * 64
    game = _game(content_hash)
    media = tmp_path / "asset_test.wav"
    media.write_bytes(b"RIFF-test-audio-bytes")
    media_sha = exports._sha256_bytes(media.read_bytes())
    row = {
        "id": "asset_test",
        "kind": "audio",
        "label": "Test ambience",
        "role": "ambient",
        "media_url": "media/asset_test.wav",
        "mime_type": "audio/wav",
        "sha256": media_sha,
        "byte_size": media.stat().st_size,
    }

    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(exports, "asset_publication_blockers", lambda _game_id: [])
    monkeypatch.setattr(exports, "private_play_html", lambda _game: "<!doctype html><title>Reviewed Game Runtime</title>")
    monkeypatch.setattr(exports, "runtime_asset_manifest", lambda _game_id: [row])
    monkeypatch.setattr(
        exports,
        "private_runtime_asset_path",
        lambda _game_id, _filename: (media, SimpleNamespace(label="Test ambience")),
    )
    monkeypatch.setattr(exports, "_brand_art_bytes", lambda: _fake_webp())
    monkeypatch.setattr(exports, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")

    first = exports.create_aura_web_export(game)
    first_bytes = (tmp_path / first["filename"]).read_bytes()
    second = exports.create_aura_web_export(game)
    second_bytes = (tmp_path / second["filename"]).read_bytes()

    assert first["export_id"] == second["export_id"]
    assert first["sha256"] == second["sha256"]
    assert first_bytes == second_bytes
    assert first["deterministic_for_current_build"] is True
    assert first["pwa_installable"] is True
    assert first["offline_core_cache"] is True
    assert first["verified_media_cache"] == "same_origin_on_demand"

    with ZipFile(tmp_path / first["filename"]) as zf:
        assert set(zf.namelist()) == {
            "index.html",
            "play.html",
            "manifest.webmanifest",
            "service-worker.js",
            "brand-icon.webp",
            "manifest.json",
            "media/asset_test.wav",
        }
        assert zf.read("play.html") == b"<!doctype html><title>Reviewed Game Runtime</title>"
        assert zf.read("brand-icon.webp") == _fake_webp()

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == exports.AURA_WEB_EXPORT_VERSION
        assert manifest["launch"]["entrypoint"] == "index.html"
        assert manifest["launch"]["runtime_entrypoint"] == "play.html"
        assert manifest["pwa"]["installable_shell"] is True
        assert manifest["pwa"]["service_worker_external_origins_allowed"] is False
        assert manifest["provenance"]["server_secrets_included"] is False
        assert manifest["provenance"]["external_network_dependency_added"] is False
        assert "rights confirmed" not in json.dumps(manifest)

        webmanifest = json.loads(zf.read("manifest.webmanifest"))
        assert webmanifest["start_url"] == "./index.html"
        assert webmanifest["display"] == "standalone"
        assert webmanifest["icons"][0]["src"] == "./brand-icon.webp"
        assert webmanifest["icons"][0]["sizes"] == "384x384"

        index = zf.read("index.html").decode("utf-8")
        assert "sandbox='allow-scripts allow-pointer-lock'" in index
        assert "serviceWorker.register('./service-worker.js'" in index
        assert "frame-src 'self'" in index
        assert "worker-src 'self'" in index

        worker = zf.read("service-worker.js").decode("utf-8")
        assert "media/asset_test.wav" in worker
        assert "ALLOWED=new Set" in worker
        assert "url.origin!==scope.origin" in worker
        assert "http://" not in worker
        assert "https://" not in worker
        assert zf.read("media/asset_test.wav") == media.read_bytes()


def test_export_format_version_changes_deterministic_identity(monkeypatch, tmp_path: Path):
    content_hash = "f" * 64
    game = _game(content_hash)
    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(exports, "asset_publication_blockers", lambda _game_id: [])
    monkeypatch.setattr(exports, "private_play_html", lambda _game: "<html></html>")
    monkeypatch.setattr(exports, "runtime_asset_manifest", lambda _game_id: [])
    monkeypatch.setattr(exports, "_brand_art_bytes", lambda: _fake_webp())
    monkeypatch.setattr(exports, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")
    current = exports.create_aura_web_export(game)
    legacy_seed = f"aura_web:{game.id}:{content_hash}".encode("utf-8")
    legacy_id = f"export_{exports.hashlib.sha256(legacy_seed).hexdigest()[:32]}"
    assert current["export_id"] != legacy_id


def test_checksum_mismatch_blocks_export(tmp_path: Path, monkeypatch):
    content_hash = "e" * 64
    game = _game(content_hash)
    media = tmp_path / "asset_bad.wav"
    media.write_bytes(b"changed")
    row = {
        "id": "asset_bad",
        "kind": "audio",
        "label": "Bad asset",
        "role": "ambient",
        "media_url": "media/asset_bad.wav",
        "mime_type": "audio/wav",
        "sha256": "0" * 64,
        "byte_size": media.stat().st_size,
    }
    monkeypatch.setattr(exports, "game_integrity_hash", lambda _game: content_hash)
    monkeypatch.setattr(exports, "asset_publication_blockers", lambda _game_id: [])
    monkeypatch.setattr(exports, "private_play_html", lambda _game: "<html></html>")
    monkeypatch.setattr(exports, "runtime_asset_manifest", lambda _game_id: [row])
    monkeypatch.setattr(exports, "private_runtime_asset_path", lambda *_: (media, SimpleNamespace(label="Bad asset")))
    monkeypatch.setattr(exports, "_brand_art_bytes", lambda: _fake_webp())
    monkeypatch.setattr(exports, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")
    with pytest.raises(ValueError, match="integrity verification"):
        exports.create_aura_web_export(game)
