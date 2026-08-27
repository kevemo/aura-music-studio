from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_assets as assets
from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import render_aura3d_playtest
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP, render_foundation_playtest
from aura_music_studio.game_forge_world import generate_foundation_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="runtime-media-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public-games"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _install_asset(game: GameDNA, *, kind: str, suffix: str, role: str, payload: bytes):
    digest = hashlib.sha256(payload).hexdigest()
    record = assets.GameAssetRecord(
        game_id=game.id,
        kind=kind,
        label=f"Verified {kind}",
        role=role,
        source_type="generated",
        source_project="runtime-media-test",
        source_element_id=f"element-{kind}",
        source_element_updated_at="2026-08-27T00:00:00+00:00",
        source_media_sha256=digest,
        imported_filename="pending",
        byte_size=len(payload),
        rights_confirmed=True,
        rights_attestation="Original test media with publishing rights confirmed.",
    )
    record.imported_filename = f"{record.id}{suffix}"
    path = assets._asset_file(game.id, record)
    path.write_bytes(payload)
    manifest = assets.load_asset_manifest(game.id)
    manifest.assets.append(record)
    assets.save_asset_manifest(manifest)
    return record, path


def test_aura2d_consumes_verified_image_audio_and_video_snapshots(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Media World 2D",
        prompt="Use verified creative media",
        dimension="2d",
        engine_target="aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    image, _ = _install_asset(game, kind="image", suffix=".png", role="world background", payload=b"png-snapshot")
    music, _ = _install_asset(game, kind="music", suffix=".wav", role="soundtrack", payload=b"wav-snapshot")
    video, _ = _install_asset(game, kind="video", suffix=".mp4", role="intro cutscene", payload=b"mp4-snapshot")

    html = render_foundation_playtest(game)

    assert f"media/{image.imported_filename}" in html
    assert f"media/{music.imported_filename}" in html
    assert f"media/{video.imported_filename}" in html
    assert "new Image()" in html
    assert "new Audio(" in html
    assert "drawBackdrop" in html
    assert "Play cutscene" in html
    assert "img-src 'self' data: blob:" in html
    assert "media-src 'self' data: blob:" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html


def test_aura3d_consumes_verified_texture_soundtrack_and_cutscene(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Media World 3D",
        prompt="Use a verified terrain texture and soundtrack",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    image, _ = _install_asset(game, kind="image", suffix=".webp", role="terrain texture", payload=b"webp-snapshot")
    music, _ = _install_asset(game, kind="music", suffix=".mp3", role="ambient soundtrack", payload=b"mp3-snapshot")
    video, _ = _install_asset(game, kind="video", suffix=".webm", role="cinematic cutscene", payload=b"webm-snapshot")
    world = generate_foundation_world(game)

    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)

    assert f"media/{image.imported_filename}" in html
    assert f"media/{music.imported_filename}" in html
    assert f"media/{video.imported_filename}" in html
    assert "uniform sampler2D uBaseColorMap" in html
    assert "gl.texImage2D" in html
    assert "uUseBaseColorMap" in html
    assert "new Audio(" in html
    assert "Play cutscene" in html
    assert '"same_origin_verified_media_only": true' in html
    assert '"network_access": false' in html
    assert '"runtime_version": 4' in html
    assert '"declarative_cinematics": true' in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html


def test_public_snapshot_copies_exact_verified_media_without_private_metadata(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Public Media", prompt="Snapshot media", rights_confirmed=True)
    store.create_game(_member(), game)
    record, source = _install_asset(game, kind="music", suffix=".flac", role="soundtrack", payload=b"immutable-public-media")
    public_id = "public_game_runtime_media"
    store.public_dir(public_id, must_exist=False).mkdir(parents=True)

    rows = assets.snapshot_public_assets(game.id, public_id)
    published, public_row = assets.public_runtime_asset_path(public_id, record.imported_filename)

    assert rows[0]["id"] == record.id
    assert published.read_bytes() == source.read_bytes()
    assert public_row["sha256"] == record.source_media_sha256
    public_manifest_text = (store.public_dir(public_id) / "assets.json").read_text(encoding="utf-8")
    assert "runtime-media-test" not in public_manifest_text
    assert "rights_attestation" not in public_manifest_text
    assert "source_element_id" not in public_manifest_text
    assert "creator_private_data_included" in public_manifest_text
    assert "external_media_urls_included" in public_manifest_text

    with pytest.raises(FileNotFoundError):
        assets.public_runtime_asset_path(public_id, "../private.flac")


def test_public_snapshot_refuses_tampered_private_asset(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Tamper Guard", prompt="Guard media", rights_confirmed=True)
    store.create_game(_member(), game)
    _record, source = _install_asset(game, kind="image", suffix=".jpg", role="background", payload=b"original-image")
    source.write_bytes(b"tampered-image")
    public_id = "public_game_tamper_guard"
    store.public_dir(public_id, must_exist=False).mkdir(parents=True)

    with pytest.raises(ValueError, match="integrity verification"):
        assets.snapshot_public_assets(game.id, public_id)


def test_runtime_asset_projection_is_relative_and_path_free(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Projection", prompt="Project media", rights_confirmed=True)
    store.create_game(_member(), game)
    record, _ = _install_asset(game, kind="audio", suffix=".ogg", role="sfx", payload=b"ogg-snapshot")

    row = assets.runtime_asset_manifest(game.id)[0]
    assert row["media_url"] == f"media/{record.imported_filename}"
    assert not row["media_url"].startswith("/")
    assert "source_project" not in row
    assert "source_element_id" not in row
    assert "rights_attestation" not in row
    assert "imported_filename" not in row
