from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app as production_entrypoint
import aura_music_studio.creative_studio_integration as integration
from aura_music_studio.assets import AssetLibrary
from aura_music_studio.creative_project import CreativeDirective, CreativeProjectStore, CreativeReference
from aura_music_studio.creative_project_api import QueueRendererRequest
from aura_music_studio.creative_renderers import RendererInput
from aura_music_studio.render_attempts import RenderAttemptStore


class _Plan:
    id = "ultimate_pro"

    def has(self, _capability: str) -> bool:
        return True


_MEMBER = SimpleNamespace(user_id="chat3-member", plan=_Plan())


def _client(monkeypatch, tmp_path: Path):
    root = tmp_path / "projects"
    root.mkdir()

    def resolve(name: str, *, must_exist: bool = False):
        target = root / name
        if must_exist and not target.exists():
            raise FileNotFoundError(target)
        return target

    monkeypatch.setattr(integration, "project_path", resolve)
    app = FastAPI()

    @app.middleware("http")
    async def member_context(request, call_next):
        request.state.member = _MEMBER
        return await call_next(request)

    app.include_router(integration.router)
    app.add_middleware(integration.CreativeStudioIntegrationMiddleware)
    return TestClient(app), root


def _init(root: Path, project_name: str = "visual-project") -> CreativeProjectStore:
    project = root / project_name
    store = CreativeProjectStore(project)
    store.initialize(project_name=project_name, title="Visual Project", project_intent="One shared creative project")
    return store


def test_reference_upload_is_one_shared_project_asset_and_creative_dna_reference(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)

    response = client.post(
        "/creative/projects/visual-project/references/upload",
        files={"file": ("portrait.png", b"same-image-content", "image/png")},
        data={
            "kind": "image",
            "label": "Portrait",
            "usage": "Keep the subject identity and pose",
            "rights_confirmed": "true",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shared_project_asset"] is True
    assert body["reference"]["source_ref"] == f"asset:{body['asset']['id']}"
    assert body["reference"]["metadata"]["asset_id"] == body["asset"]["id"]
    assert "path" not in body["asset"]
    assert body["reference"]["rights_confirmed"] is True

    manifest = store.load()
    assert manifest.references[-1].id == body["reference"]["id"]
    assets = AssetLibrary(store.project_dir).list()
    assert [asset.id for asset in assets] == [body["asset"]["id"]]
    assert assets[0].rights_record_id


def test_duplicate_reference_upload_reuses_asset_identity_without_breaking_older_reference(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)
    payload = {
        "kind": "image",
        "label": "Same portrait",
        "usage": "Identity reference",
        "rights_confirmed": "true",
    }

    first = client.post(
        "/creative/projects/visual-project/references/upload",
        files={"file": ("portrait.png", b"duplicate-content", "image/png")},
        data=payload,
    )
    second = client.post(
        "/creative/projects/visual-project/references/upload",
        files={"file": ("portrait-again.png", b"duplicate-content", "image/png")},
        data=payload,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["asset"]["id"] == second.json()["asset"]["id"]
    assert first.json()["reference"]["id"] != second.json()["reference"]["id"]
    assert second.json()["reused_project_asset"] is True
    assert len(AssetLibrary(store.project_dir).list()) == 1
    refs = store.load().references
    assert refs[0].source_ref == refs[1].source_ref


def test_reference_upload_requires_rights_and_rejects_wrong_kind_extension(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    _init(root)

    missing_rights = client.post(
        "/creative/projects/visual-project/references/upload",
        files={"file": ("portrait.png", b"image", "image/png")},
        data={"kind": "image", "rights_confirmed": "false"},
    )
    wrong_kind = client.post(
        "/creative/projects/visual-project/references/upload",
        files={"file": ("clip.mp4", b"video", "video/mp4")},
        data={"kind": "image", "rights_confirmed": "true"},
    )

    assert missing_rights.status_code == 400
    assert "right or authorization" in missing_rights.json()["detail"]
    assert wrong_kind.status_code == 400
    assert "Unsupported image reference file type" in wrong_kind.json()["detail"]


def test_local_reference_marker_is_converted_to_opaque_token_only_inside_submit_stage(tmp_path):
    source = tmp_path / "member-reference.png"
    source.write_bytes(b"reference")
    marker = integration.LocalRendererImageInput(
        source=source,
        reference_id="ref-1",
        asset_id="asset-1",
    )

    class FakeRenderer:
        def upload_image_input(self, path: Path):
            assert path == source
            return RendererInput(name="opaque.png", workflow_value="opaque/subfolder.png")

    resolved = integration._resolve_local_renderer_inputs(
        FakeRenderer(),
        {"prompt": "keep identity", "reference_image": marker, "reference_image_count": 1},
    )

    assert resolved["reference_image"] == "opaque/subfolder.png"
    assert resolved["reference_image_count"] == 1
    assert resolved["prompt"] == "keep identity"
    assert str(source) not in str(resolved)


def test_integrated_render_defers_image_staging_until_commercial_authority_calls_submit(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)
    source = store.project_dir / "reference.png"
    source.write_bytes(b"reference-image")
    asset = AssetLibrary(store.project_dir).ingest(source, kind="image")
    reference = CreativeReference(
        kind="image",
        label="Identity reference",
        source_ref=f"asset:{asset.id}",
        usage="Keep face and pose",
        rights_confirmed=True,
        metadata={"asset_id": asset.id},
    )
    store.add_reference(reference)
    directive = CreativeDirective(
        instruction="Create a cinematic portrait while preserving the referenced identity.",
        operation="create",
        target_kind="image",
        reference_ids=[reference.id],
    )
    store.add_directive(directive)

    captured = {"commercial_calls": 0}

    def fake_commercial_queue(project_name, directive_id, body: QueueRendererRequest, request):
        captured["commercial_calls"] += 1
        captured.update(body.variables)
        marker = body.variables["reference_image"]
        assert isinstance(marker, integration.LocalRendererImageInput)
        assert marker.reference_id == reference.id
        assert marker.asset_id == asset.id
        assert marker.source.is_file()
        manifest = store.update_directive(
            directive_id,
            status="queued",
            capability_state="connected",
            metadata={"creative_renderer": {"prompt_id": "prompt-1", "workflow_name": "image.json"}},
        )
        current = next(item for item in manifest.directives if item.id == directive_id)
        return {"directive": current.model_dump(mode="json"), "note": "Commercial authority admitted the render."}

    monkeypatch.setattr(integration, "render_with_commercial_entitlements", fake_commercial_queue)

    response = client.post(
        f"/creative/projects/visual-project/directives/{directive.id}/render-integrated",
        json={"width": 1024, "height": 1024, "frames": 1, "fps": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert captured["commercial_calls"] == 1
    assert isinstance(captured["reference_image"], integration.LocalRendererImageInput)
    assert captured["reference_image_count"] == 1
    assert body["reference_input_count"] == 1
    assert body["reference_inputs"] == [
        {"reference_id": reference.id, "asset_id": asset.id, "workflow_variable": "reference_image"}
    ]
    assert str(store.project_dir) not in response.text
    meta = body["directive"]["metadata"]["creative_renderer"]
    assert meta["staged_image_reference_count"] == 1
    assert meta["staged_image_reference_ids"] == [reference.id]


def test_commercial_denial_does_not_stage_reference_to_renderer(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)
    source = store.project_dir / "denied.png"
    source.write_bytes(b"denied-reference")
    asset = AssetLibrary(store.project_dir).ingest(source, kind="image")
    reference = CreativeReference(
        kind="image",
        label="Denied reference",
        source_ref=f"asset:{asset.id}",
        rights_confirmed=True,
        metadata={"asset_id": asset.id},
    )
    store.add_reference(reference)
    directive = CreativeDirective(
        instruction="Create image",
        operation="create",
        target_kind="image",
        reference_ids=[reference.id],
    )
    store.add_directive(directive)
    staged = {"count": 0}

    def denied(*_args, **_kwargs):
        raise integration.HTTPException(402, "Creation Coin admission denied")

    def should_not_stage(*_args, **_kwargs):
        staged["count"] += 1
        raise AssertionError("renderer staging must not occur before admission")

    monkeypatch.setattr(integration, "render_with_commercial_entitlements", denied)
    monkeypatch.setattr(integration, "_resolve_local_renderer_inputs", should_not_stage)

    response = client.post(
        f"/creative/projects/visual-project/directives/{directive.id}/render-integrated",
        json={"width": 1024, "height": 1024, "frames": 1, "fps": 1},
    )

    assert response.status_code == 402
    assert staged["count"] == 0


def test_cancel_integrated_releases_matching_durable_attempt_without_fabricating_refund(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)
    directive = CreativeDirective(
        instruction="Create image",
        operation="create",
        target_kind="image",
    )
    manifest = store.add_directive(directive)
    store.update_directive(
        directive.id,
        status="queued",
        capability_state="connected",
        metadata={"creative_renderer": {"prompt_id": "prompt-cancel-1"}},
    )

    attempts = RenderAttemptStore(tmp_path / "attempts.sqlite3")
    attempt = attempts.reserve(_MEMBER.user_id, "visual-project", directive.id)
    attempts.mark_queued(attempt.attempt_id, "prompt-cancel-1")
    monkeypatch.setattr(integration, "RenderAttemptStore", lambda: attempts)

    def fake_cancel(project_name, directive_id, request):
        updated = store.update_directive(
            directive_id,
            status="ready_for_renderer",
            metadata={
                "creative_renderer": {
                    "prompt_id": "prompt-cancel-1",
                    "cancelled": True,
                    "cancellation_state": "cancelled_pending",
                }
            },
        )
        current = next(item for item in updated.directives if item.id == directive_id)
        return {
            "directive": current.model_dump(mode="json"),
            "cancellation": {"state": "cancelled_pending", "prompt_id": "prompt-cancel-1"},
            "note": "Creative render cancelled safely.",
        }

    monkeypatch.setattr(integration, "cancel_creative_render", fake_cancel)

    response = client.post(
        f"/creative/projects/visual-project/directives/{directive.id}/cancel-integrated",
        json={},
    )

    assert response.status_code == 200, response.text
    reconciliation = response.json()["render_attempt_reconciliation"]
    assert reconciliation["state"] == "failed"
    assert reconciliation["provider_prompt_matched"] is True
    assert reconciliation["refund_issued"] is False
    assert attempts.active(_MEMBER.user_id, "visual-project", directive.id) is None
    assert attempts.get(attempt.attempt_id).state == "failed"


def test_cancel_integrated_fails_closed_when_provider_prompt_identity_does_not_match(monkeypatch, tmp_path):
    client, root = _client(monkeypatch, tmp_path)
    store = _init(root)
    directive = CreativeDirective(
        instruction="Create image",
        operation="create",
        target_kind="image",
    )
    store.add_directive(directive)
    attempts = RenderAttemptStore(tmp_path / "attempts-mismatch.sqlite3")
    attempt = attempts.reserve(_MEMBER.user_id, "visual-project", directive.id)
    attempts.mark_queued(attempt.attempt_id, "ledger-prompt")
    monkeypatch.setattr(integration, "RenderAttemptStore", lambda: attempts)

    def fake_cancel(project_name, directive_id, request):
        updated = store.update_directive(
            directive_id,
            status="ready_for_renderer",
            metadata={"creative_renderer": {"prompt_id": "different-prompt", "cancelled": True}},
        )
        current = next(item for item in updated.directives if item.id == directive_id)
        return {
            "directive": current.model_dump(mode="json"),
            "cancellation": {"state": "cancelled_pending", "prompt_id": "different-prompt"},
        }

    monkeypatch.setattr(integration, "cancel_creative_render", fake_cancel)

    response = client.post(
        f"/creative/projects/visual-project/directives/{directive.id}/cancel-integrated",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["render_attempt_reconciliation"] is None
    assert attempts.active(_MEMBER.user_id, "visual-project", directive.id) is not None


def test_integration_ui_is_injected_into_all_three_chat3_surfaces(monkeypatch, tmp_path):
    client, _root = _client(monkeypatch, tmp_path)

    @client.app.get("/creative-house")
    def creative_house():
        return integration.Response("<html><body>Creative House</body></html>", media_type="text/html")

    @client.app.get("/image-designer")
    def image_designer():
        return integration.Response("<html><body>Image Designer</body></html>", media_type="text/html")

    @client.app.get("/video-studio")
    def video_studio():
        return integration.Response("<html><body>Video Studio</body></html>", media_type="text/html")

    for path in ("/creative-house", "/image-designer", "/video-studio"):
        response = client.get(path)
        assert response.status_code == 200
        assert "<script src='/creative/studio-integration-ui.js'></script>" in response.text

    script = client.get("/creative/studio-integration-ui.js")
    assert script.status_code == 200
    assert "/references/upload" in script.text
    assert "/render-integrated" in script.text
    assert "/cancel-integrated" in script.text
    assert "Live monitor active" in script.text
    assert "Upload & attach reference" in script.text
    assert "inferredUploadKind" in script.text
    assert "cancelRender=cancelIntegrated" in script.text


def test_production_entrypoint_mounts_unified_creative_bridge_on_same_site():
    paths = production_entrypoint.app.openapi().get("paths", {})
    assert "/creative/projects/{project_name}/references/upload" in paths
    assert "/creative/projects/{project_name}/directives/{directive_id}/render-integrated" in paths
    assert "/creative/projects/{project_name}/directives/{directive_id}/cancel-integrated" in paths
