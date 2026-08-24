from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.aura_agent_tools import AuraToolRegistry, ToolCall
from aura_music_studio.aura_multimodal import AuraVisionService
from aura_music_studio.aura_tool_extensions import install_aura_tool_extensions
from aura_music_studio.creative_project import CreativeProjectStore


def _member():
    return SimpleNamespace(
        user_id="member-test",
        plan=SimpleNamespace(has=lambda feature: True),
    )


def test_visual_request_is_saved_truthfully_when_renderer_unconfigured(tmp_path, monkeypatch):
    install_aura_tool_extensions()
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    monkeypatch.delenv("AURA_COMFYUI_URL", raising=False)
    monkeypatch.delenv("AURA_COMFYUI_IMAGE_WORKFLOW", raising=False)

    project = tmp_path / "visual"
    project.mkdir()
    registry = AuraToolRegistry(
        member=_member(),
        pinned_project="visual",
        web_enabled=False,
        tools_enabled=True,
    )
    call = ToolCall(
        name="create_visual",
        arguments={
            "kind": "image",
            "prompt": "A cosmic professional music poster with a pulsar and film reel",
            "width": 1080,
            "height": 1920,
        },
    )

    first = registry.execute(call, latest_user_message="Create a 9:16 cosmic image poster")
    second = registry.execute(call, latest_user_message="Create a 9:16 cosmic image poster")

    assert first["renderer_configured"] is False
    assert first["queued"] is False
    assert second["renderer_configured"] is False
    manifest = CreativeProjectStore(project).load()
    assert len(manifest.directives) == 1
    assert manifest.directives[0].target_kind == "image"
    assert manifest.directives[0].status in {"planned", "ready_for_renderer"}


def test_renderer_status_never_exposes_internal_base_url(monkeypatch):
    install_aura_tool_extensions()
    monkeypatch.delenv("AURA_COMFYUI_URL", raising=False)
    registry = AuraToolRegistry(member=_member(), pinned_project=None, web_enabled=False, tools_enabled=True)

    result = registry.execute(
        ToolCall(name="creative_renderer_status", arguments={"probe": False}),
        latest_user_message="What creative renderers are available?",
    )

    assert set(result) >= {"image", "video"}
    assert all("base_url" not in state for state in result.values())


def test_visual_write_requires_explicit_latest_user_instruction(tmp_path, monkeypatch):
    install_aura_tool_extensions()
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    (tmp_path / "visual").mkdir()
    registry = AuraToolRegistry(member=_member(), pinned_project="visual", web_enabled=False, tools_enabled=True)

    with pytest.raises(PermissionError):
        registry.execute(
            ToolCall(name="create_visual", arguments={"kind": "image", "prompt": "Cosmic poster"}),
            latest_user_message="What would a cosmic poster look like?",
        )


def test_vision_reports_unconfigured_instead_of_faking_analysis(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_VISION_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    service = AuraVisionService()
    image = tmp_path / "fake.png"
    image.write_bytes(b"not-needed-because-service-is-unconfigured")

    assert service.configured is False
    with pytest.raises(RuntimeError, match="not configured"):
        service.analyze_images([image], "Describe this image")
