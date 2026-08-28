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


def test_external_adapters_are_truthfully_not_production_ready():
    caps = exports.export_capabilities()
    assert caps["production_ready_targets"] == ["aura_web"]
    assert caps["external_adapters_claimed_ready"] is False
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


def test_same_build_and_media_produce_identical_export_bytes(tmp_path: Path, monkeypatch):
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
    monkeypatch.setattr(exports, "private_play_html", lambda _game: "<!doctype html><title>Game</title>")
    monkeypatch.setattr(exports, "runtime_asset_manifest", lambda _game_id: [row])
    monkeypatch.setattr(
        exports,
        "private_runtime_asset_path",
        lambda _game_id, _filename: (media, SimpleNamespace(label="Test ambience")),
    )
    monkeypatch.setattr(exports, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")

    first = exports.create_aura_web_export(game)
    first_bytes = (tmp_path / first["filename"]).read_bytes()
    second = exports.create_aura_web_export(game)
    second_bytes = (tmp_path / second["filename"]).read_bytes()

    assert first["export_id"] == second["export_id"]
    assert first["sha256"] == second["sha256"]
    assert first_bytes == second_bytes
    assert first["deterministic_for_current_build"] is True

    with ZipFile(tmp_path / first["filename"]) as zf:
        assert set(zf.namelist()) == {"play.html", "manifest.json", "media/asset_test.wav"}
        manifest = zf.read("manifest.json").decode("utf-8")
        assert '"content_integrity_bound": true' in manifest
        assert '"server_secrets_included": false' in manifest
        assert "rights confirmed" not in manifest
        assert zf.read("media/asset_test.wav") == media.read_bytes()


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
    monkeypatch.setattr(exports, "_export_path", lambda _game_id, export_id: tmp_path / f"{export_id}.zip")
    with pytest.raises(ValueError, match="integrity verification"):
        exports.create_aura_web_export(game)
