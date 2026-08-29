from __future__ import annotations

from aura_music_studio.creative_renderers import ComfyUIRenderer, RendererSubmission
import aura_music_studio.provider_cost_governance as governance
from aura_music_studio.provider_cost_governance import ProviderCostStore


def _submission(kind: str = "video") -> RendererSubmission:
    return RendererSubmission(
        kind=kind,
        provider="comfyui",
        prompt_id="opaque-provider-job-123",
        client_id="client-123",
        workflow_name="video.json",
    )


def _renderer(kind: str = "video") -> ComfyUIRenderer:
    renderer = object.__new__(ComfyUIRenderer)
    renderer.kind = kind
    return renderer


def test_successful_renderer_submission_is_metered_once(tmp_path, monkeypatch):
    cost_store = ProviderCostStore(tmp_path / "costs.sqlite3")
    monkeypatch.setattr(governance, "store", cost_store)

    def fake_submit(self, variables):
        return _submission(self.kind)

    monkeypatch.setattr(ComfyUIRenderer, "submit", fake_submit)
    governance.install_provider_cost_governance()

    renderer = _renderer("video")
    variables = {
        "operation": "create",
        "frames": 121,
        "user_id": "member-raw-id",
        "project_name": "private-project-name",
    }
    first = renderer.submit(variables)
    second = renderer.submit(variables)

    assert first.prompt_id == "opaque-provider-job-123"
    assert second.prompt_id == first.prompt_id
    recent = cost_store.recent()
    assert len(recent) == 1
    assert recent[0]["provider"] == "comfyui"
    assert recent[0]["service"] == "video"
    assert recent[0]["operation"] == "create"
    assert recent[0]["unit_name"] == "frames"
    assert recent[0]["units"] == 121


def test_metering_failure_never_converts_successful_render_into_failure(monkeypatch):
    class FailingStore:
        def record_submission(self, **kwargs):
            raise RuntimeError("meter unavailable")

    monkeypatch.setattr(governance, "store", FailingStore())

    def fake_submit(self, variables):
        return _submission(self.kind)

    monkeypatch.setattr(ComfyUIRenderer, "submit", fake_submit)
    governance.install_provider_cost_governance()

    result = _renderer("image").submit({"operation": "render"})
    assert result.prompt_id == "opaque-provider-job-123"


def test_installer_is_idempotent(tmp_path, monkeypatch):
    cost_store = ProviderCostStore(tmp_path / "costs.sqlite3")
    monkeypatch.setattr(governance, "store", cost_store)

    calls = {"provider": 0}

    def fake_submit(self, variables):
        calls["provider"] += 1
        return _submission(self.kind)

    monkeypatch.setattr(ComfyUIRenderer, "submit", fake_submit)
    governance.install_provider_cost_governance()
    governed = ComfyUIRenderer.submit
    governance.install_provider_cost_governance()

    assert ComfyUIRenderer.submit is governed
    _renderer("video").submit({"operation": "render", "frames": 24})
    assert calls["provider"] == 1
    assert len(cost_store.recent()) == 1
