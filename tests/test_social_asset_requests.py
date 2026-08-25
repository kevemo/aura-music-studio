from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from aura_music_studio.esp_social_asset_requests import (
    CreateAssetRequest,
    asset_request_snapshot,
    create_asset_request,
    list_requested_assets,
    revoke_asset_request,
    save_requested_asset,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import SocialHouseStore


def _setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_ASSET_REQUEST_DB", str(tmp_path / "asset_requests.sqlite3"))
    monkeypatch.setenv("AURA_SOCIAL_ASSET_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("AURA_SOCIAL_ASSET_MAX_MB", "1")
    token = set_current_user_id("esp-owner")
    try:
        house = SocialHouseStore().create_space("Client Assets")
    finally:
        reset_current_user_id(token)
    request = create_asset_request("esp-owner", CreateAssetRequest(space_id=house.id, title="Send raw media", expires_hours=24))
    return house.id, request


@pytest.mark.asyncio
async def test_asset_request_requires_rights_and_quarantines_upload(tmp_path: Path, monkeypatch):
    _space, request = _setup(tmp_path, monkeypatch)
    denied = UploadFile(filename="photo.jpg", file=io.BytesIO(b"fake-image"), headers={"content-type": "image/jpeg"})
    with pytest.raises(ValueError, match="Rights confirmation"):
        await save_requested_asset(request["token"], denied, uploader_name="Client", rights_confirmed=False)

    upload = UploadFile(filename="photo.jpg", file=io.BytesIO(b"fake-image"), headers={"content-type": "image/jpeg"})
    saved = await save_requested_asset(request["token"], upload, uploader_name="Client", rights_confirmed=True)
    assert saved["status"] == "pending_review"
    assert saved["rights_confirmed"] is True
    assert saved["attached_to_project"] is False
    assert saved["published"] is False

    assets = list_requested_assets("esp-owner", request["id"])
    assert len(assets) == 1
    assert assets[0]["original_name"] == "photo.jpg"
    assert assets[0]["status"] == "pending_review"
    assert "authorised" in assets[0]["rights_statement"]


@pytest.mark.asyncio
async def test_asset_upload_type_and_size_are_bounded(tmp_path: Path, monkeypatch):
    _space, request = _setup(tmp_path, monkeypatch)
    bad = UploadFile(filename="payload.exe", file=io.BytesIO(b"x"), headers={"content-type": "application/octet-stream"})
    with pytest.raises(ValueError, match="Unsupported file type"):
        await save_requested_asset(request["token"], bad, uploader_name="Client", rights_confirmed=True)

    big = UploadFile(filename="big.mp4", file=io.BytesIO(b"x" * (1024 * 1024 + 1)), headers={"content-type": "video/mp4"})
    with pytest.raises(ValueError, match="upload limit"):
        await save_requested_asset(request["token"], big, uploader_name="Client", rights_confirmed=True)
    assert not list((tmp_path / "assets").rglob("big.mp4"))


def test_asset_request_token_is_hashed_and_revoke_is_owner_scoped(tmp_path: Path, monkeypatch):
    _space, request = _setup(tmp_path, monkeypatch)
    conn = sqlite3.connect(tmp_path / "asset_requests.sqlite3")
    token_hash = conn.execute("SELECT token_hash FROM social_asset_requests WHERE id=?", (request["id"],)).fetchone()[0]
    conn.close()
    assert request["token"] != token_hash
    assert request["token"] not in (tmp_path / "asset_requests.sqlite3").read_bytes().decode("latin1", errors="ignore")
    assert asset_request_snapshot(request["token"])["title"] == "Send raw media"
    assert revoke_asset_request("someone-else", request["id"]) is False
    assert revoke_asset_request("esp-owner", request["id"]) is True
    with pytest.raises(PermissionError, match="revoked"):
        asset_request_snapshot(request["token"])
