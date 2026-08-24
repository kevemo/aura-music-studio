from __future__ import annotations

import pytest

import aura_music_studio.aura_workflow_engine as workflow


def test_exact_workflow_references_preserve_native_types():
    history = [
        {
            "tool": "promote_current_attachment",
            "ok": True,
            "result": {
                "creative_reference": {"id": "ref-123", "metadata": {"score": 0.95}},
                "items": ["a", "b"],
            },
        }
    ]
    assert workflow.resolve_workflow_value("$step0.creative_reference.id", history) == "ref-123"
    assert workflow.resolve_workflow_value("$previous.items", history) == ["a", "b"]
    assert workflow.resolve_workflow_value({"refs": ["$step0.creative_reference.id"]}, history) == {"refs": ["ref-123"]}
    assert workflow.resolve_workflow_value("reference=$step0.creative_reference.id", history) == "reference=ref-123"


def test_workflow_reference_cannot_read_future_or_failed_steps():
    success = [{"tool": "one", "ok": True, "result": {"id": "x"}}]
    with pytest.raises(ValueError, match="has not completed"):
        workflow.resolve_workflow_value("$step1.id", success)
    with pytest.raises(KeyError, match="no field"):
        workflow.resolve_workflow_value("$step0.secret", success)

    failed = [{"tool": "one", "ok": False, "error": "boom"}]
    with pytest.raises(RuntimeError, match="failed"):
        workflow.resolve_workflow_value("$step0.id", failed)


def test_embedded_structured_result_is_rejected():
    history = [{"tool": "one", "ok": True, "result": {"payload": {"a": 1}}}]
    with pytest.raises(ValueError, match="Structured workflow results"):
        workflow.resolve_workflow_value("payload=$step0.payload", history)


def test_visual_dimensions_follow_common_social_aspect_ratios():
    assert workflow._dimensions("make this 9:16", "image") == (1080, 1920)
    assert workflow._dimensions("16:9 landscape", "video") == (1920, 1080)
    assert workflow._dimensions("square 1:1", "image") == (1024, 1024)
    assert workflow._dimensions("Instagram 4:5", "image") == (1080, 1350)


def test_owned_single_attachment_visual_plan_uses_verified_reference(monkeypatch):
    monkeypatch.setattr(
        workflow,
        "latest_attachments",
        lambda: [{"id": "attachment-1", "name": "reference.png", "kind": "image"}],
    )
    text = "I own this image. Add it to the project and make a 9:16 cinematic poster from it."
    plan = workflow._attachment_visual_plan(text, "project-one")
    assert plan is not None
    assert [call.name for call in plan.calls] == ["promote_current_attachment", "create_visual"]
    assert plan.calls[0].arguments["rights_confirmed"] is True
    assert plan.calls[1].arguments["reference_ids"] == ["$step0.creative_reference.id"]
    assert plan.calls[1].arguments["width"] == 1080
    assert plan.calls[1].arguments["height"] == 1920


def test_visual_plan_requires_member_rights_wording(monkeypatch):
    monkeypatch.setattr(workflow, "latest_attachments", lambda: [{"id": "a", "name": "ref.png"}])
    assert workflow._attachment_visual_plan("Add this to the project and make a poster", "project-one") is None
