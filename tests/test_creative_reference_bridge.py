from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import aura_music_studio.creative_reference_bridge as bridge
from aura_music_studio.assets import AssetLibrary
from aura_music_studio.creative_media_preview import CreativeMediaPreviewMiddleware
from aura_music_studio.creative_project import CreativeProjectStore


def _request():
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(
                user_id="member-reference-bridge",
                plan=SimpleNamespace(has=lambda _capability: False),
            )
        )
    )


def _project_with_image(tmp_path):
    project = tmp_path / "shared-creative-project"
    CreativeProjectStore(project).initialize(
        project_name=project.name,
        title="Shared Creative Project",
        project_intent="One project across Creative House, Image Designer and Video Studio.",
    )
    source = tmp_path / "portrait.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nreference-bytes")
    asset = AssetLibrary(project).ingest(
        source,
        kind="image",
        rights_basis="user_owned_or_licensed",
        attestation="I confirm I have the right to use this material in this project.",
        tags=["creative-reference"],
    )
    return project, asset


def test_project_asset_becomes_one_idempotent_creative_reference(monkeypatch, tmp_path):
    project, asset = _project_with_image(tmp_path)
    monkeypatch.setattr(bridge, "project_path", lambda _name, must_exist=True: project)

    body = bridge.AttachAssetReferenceRequest(
        kind="image",
        label="Portrait reference",
        usage="Preserve the subject identity and clothing",
        rights_confirmed=True,
    )
    first = bridge.attach_project_asset_as_creative_reference(project.name, asset.id, body, _request())
    second = bridge.attach_project_asset_as_creative_reference(project.name, asset.id, body, _request())

    assert first["single_project_source_of_truth"] is True
    assert first["already_attached"] is False
    assert first["reference"]["source_ref"].startswith("input/assets/")
    assert first["reference"]["metadata"]["asset_id"] == asset.id
    assert first["reference"]["metadata"]["asset_sha256"] == asset.sha256
    assert first["reference"]["metadata"]["rights_record_id"] == asset.rights_record_id
    assert second["already_attached"] is True
    manifest = CreativeProjectStore(project).load()
    assert len(manifest.references) == 1


def test_asset_kind_mismatch_is_rejected_before_creative_dna_mutation(monkeypatch, tmp_path):
    project, asset = _project_with_image(tmp_path)
    monkeypatch.setattr(bridge, "project_path", lambda _name, must_exist=True: project)

    body = bridge.AttachAssetReferenceRequest(
        kind="video",
        label="Wrong kind",
        rights_confirmed=True,
    )
    with pytest.raises(HTTPException) as exc:
        bridge.attach_project_asset_as_creative_reference(project.name, asset.id, body, _request())

    assert exc.value.status_code == 400
    assert "cannot be attached as video" in str(exc.value.detail)
    assert CreativeProjectStore(project).load().references == []


def test_reference_upload_ui_uses_existing_asset_api_then_creative_dna_bridge():
    script = bridge.REFERENCE_UPLOAD_SCRIPT

    assert "/assets`" in script
    assert "/references/from-asset/" in script
    assert "FormData" in script
    assert "rights_basis" in script
    assert "rights_confirmed:true" in script
    assert "addReferenceId" in script
    assert "same Creative DNA used by Creative House, Image Designer and Video Studio" in script


def test_reference_upload_overlay_is_injected_into_all_three_shared_surfaces():
    app = FastAPI()

    @app.get("/creative-house", response_class=HTMLResponse)
    def creative_house():
        return HTMLResponse("<html><body>Creative House</body></html>")

    @app.get("/video-studio", response_class=HTMLResponse)
    def video_studio():
        return HTMLResponse("<html><body>Video Studio</body></html>")

    @app.get("/image-designer", response_class=HTMLResponse)
    def image_designer():
        return HTMLResponse("<html><body>Image Designer</body></html>")

    app.include_router(bridge.router)
    app.add_middleware(CreativeMediaPreviewMiddleware)
    client = TestClient(app)

    creative = client.get("/creative-house").text
    video = client.get("/video-studio").text
    image = client.get("/image-designer").text

    assert "<script src='/creative/media-preview-ui.js'></script>" in creative
    for html in (creative, video, image):
        assert html.count("<script src='/creative/reference-upload-ui.js'></script>") == 1


def test_bridge_bootstraps_through_existing_creative_safety_stack():
    from aura_music_studio import creative_safety_overlay

    assert creative_safety_overlay._creative_reference_bridge is bridge
    assert getattr(CreativeMediaPreviewMiddleware, "_unified_reference_upload_installed", False) is True
