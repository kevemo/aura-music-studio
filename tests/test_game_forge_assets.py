from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_assets as assets
from aura_music_studio import game_forge_integrity as integrity
from aura_music_studio import game_forge_store as store
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore
from aura_music_studio.game_forge_assets import AttachGameAssetRequest
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.plans import get_plan


def _member(plan_id: str = "base"):
    return SimpleNamespace(plan=get_plan(plan_id), user_id=f"user-{plan_id}")


def _fixture_game_and_project(monkeypatch, tmp_path, *, rights_confirmed=True):
    games = tmp_path / "games"
    games.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(assets, "game_dir", store.game_dir)

    game = GameDNA(title="Asset World", prompt="A world with imported creative media", rights_confirmed=True)
    store.create_game(_member(), game)

    project = tmp_path / "creative-project"
    project.mkdir()
    media_dir = project / "media"
    media_dir.mkdir()
    source = media_dir / "theme.wav"
    source.write_bytes(b"RIFF-pulsar-game-theme")

    creative = CreativeProjectStore(project)
    creative.initialize(project_name="cosmic", title="Cosmic Creative Project")
    element = CreativeElement(
        kind="music",
        label="Cosmic Theme",
        role="master",
        status="ready",
        source_type="generated",
        source_ref="media/theme.wav",
    )
    creative.add_element(element)

    monkeypatch.setattr(
        assets,
        "project_path",
        lambda name, must_exist=True: project if name == "cosmic" else (_ for _ in ()).throw(FileNotFoundError(name)),
    )
    body = AttachGameAssetRequest(
        source_id=f"cosmic:{element.id}",
        role="soundtrack",
        rights_confirmed=rights_confirmed,
        rights_attestation="I own or have permission to use this media." if rights_confirmed else "",
    )
    return game, source, element, body


def test_game_asset_import_snapshots_media_and_hides_filesystem_path(monkeypatch, tmp_path):
    game, source, element, body = _fixture_game_and_project(monkeypatch, tmp_path)
    record = assets.attach_creative_asset(game, body)

    assert record.game_id == game.id
    assert record.kind == "music"
    assert record.source_element_id == element.id
    assert record.source_media_sha256 == assets._sha256(source)

    imported = assets._asset_file(game.id, record)
    assert imported.read_bytes() == source.read_bytes()

    public = assets.public_asset(record)
    assert public["filesystem_path_exposed"] is False
    assert "imported_filename" not in public
    assert "source_ref" not in public
    assert public["media_url"].endswith(f"/assets/{record.id}/media")


def test_asset_integrity_detects_tampering_and_rights_blockers(monkeypatch, tmp_path):
    game, _source, _element, body = _fixture_game_and_project(
        monkeypatch,
        tmp_path,
        rights_confirmed=False,
    )
    record = assets.attach_creative_asset(game, body)

    blockers = " ".join(assets.asset_publication_blockers(game.id)).lower()
    assert "rights confirmation" in blockers

    assets.update_game_asset_rights(
        game.id,
        record.id,
        rights_confirmed=True,
        rights_attestation="I own this original soundtrack.",
    )
    assert assets.asset_publication_blockers(game.id) == []

    assets._asset_file(game.id, record).write_bytes(b"tampered")
    blockers = " ".join(assets.asset_publication_blockers(game.id)).lower()
    assert "integrity verification" in blockers


def test_game_integrity_hash_binds_imported_assets(monkeypatch, tmp_path):
    game, _source, _element, body = _fixture_game_and_project(monkeypatch, tmp_path)
    monkeypatch.setattr(integrity, "world_rating_payload", lambda _game_id: {"world": "fixed"})

    before = integrity.game_integrity_hash(game)
    record = assets.attach_creative_asset(game, body)
    after = integrity.game_integrity_hash(game)
    assert before != after

    assets.update_game_asset_rights(
        game.id,
        record.id,
        rights_confirmed=True,
        rights_attestation="Updated rights record.",
    )
    after_rights = integrity.game_integrity_hash(game)
    assert after_rights != after


def test_asset_import_rejects_project_path_escape(monkeypatch, tmp_path):
    games = tmp_path / "games"
    games.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(assets, "game_dir", store.game_dir)
    game = GameDNA(title="Safe World", prompt="Safe", rights_confirmed=True)
    store.create_game(_member(), game)

    project = tmp_path / "creative-project"
    project.mkdir()
    creative = CreativeProjectStore(project)
    creative.initialize(project_name="cosmic", title="Cosmic Creative Project")
    element = CreativeElement(
        kind="image",
        label="Unsafe ref",
        status="ready",
        source_ref="../outside.png",
    )
    creative.add_element(element)
    (tmp_path / "outside.png").write_bytes(b"not allowed")

    monkeypatch.setattr(assets, "project_path", lambda name, must_exist=True: project)
    with pytest.raises(ValueError, match="escaped"):
        assets.attach_creative_asset(
            game,
            AttachGameAssetRequest(
                source_id=f"cosmic:{element.id}",
                rights_confirmed=True,
                rights_attestation="I own this image.",
            ),
        )


def test_asset_rights_require_attestation(monkeypatch, tmp_path):
    game, _source, _element, body = _fixture_game_and_project(monkeypatch, tmp_path)
    record = assets.attach_creative_asset(game, body)

    with pytest.raises(ValueError, match="rights attestation"):
        assets.update_game_asset_rights(
            game.id,
            record.id,
            rights_confirmed=True,
            rights_attestation="",
        )


def test_asset_router_is_composed_into_world_router():
    from fastapi import FastAPI
    from aura_music_studio.game_forge_world_api import router as world_router

    app = FastAPI()
    app.include_router(world_router)
    paths = set(app.openapi()["paths"])
    assert "/api/game-forge/games/{game_id}/assets" in paths
    assert "/api/game-forge/games/{game_id}/assets/library" in paths
