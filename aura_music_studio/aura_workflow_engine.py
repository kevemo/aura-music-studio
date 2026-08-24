from __future__ import annotations

import re
from typing import Any

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_attachment_tools import _promotion_requested, _rights_in_member_text
from .aura_runtime_context import latest_attachments

_INSTALLED = False
_EXACT_REF = re.compile(r"^\$(?:step(?P<step>\d+)|previous)(?:\.(?P<path>[A-Za-z0-9_.-]+))?$")
_EMBEDDED_REF = re.compile(r"\$(?:step(?P<step>\d+)|previous)(?:\.(?P<path>[A-Za-z0-9_.-]+))?")


def _path_value(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(f"Workflow result has no field {part!r}")
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index < 0 or index >= len(current):
                raise IndexError("Workflow result list index is out of range")
            current = current[index]
        else:
            raise KeyError(f"Cannot resolve workflow field {part!r}")
    return current


def _step_result(history: list[dict], step: int) -> Any:
    if step < 0 or step >= len(history):
        raise ValueError(f"Workflow references step{step}, but that step has not completed")
    item = history[step]
    if not item.get("ok"):
        raise RuntimeError(f"Workflow step{step} failed; dependent tool call was not executed")
    return item.get("result")


def _reference_value(token: str, history: list[dict]) -> Any:
    match = _EXACT_REF.fullmatch(token)
    if not match:
        raise ValueError("Invalid Aura workflow reference")
    if token.startswith("$previous"):
        if not history:
            raise ValueError("$previous cannot be used before a workflow step has completed")
        step = len(history) - 1
    else:
        step = int(match.group("step"))
    return _path_value(_step_result(history, step), match.group("path"))


def resolve_workflow_value(value: Any, history: list[dict]) -> Any:
    """Resolve $stepN.field or $previous.field references from verified prior results.

    Exact-reference strings preserve the underlying type. Embedded references are allowed
    only for scalar values and become bounded strings. References can never point forward.
    """
    if isinstance(value, dict):
        return {key: resolve_workflow_value(item, history) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_workflow_value(item, history) for item in value]
    if not isinstance(value, str) or "$" not in value:
        return value
    if _EXACT_REF.fullmatch(value):
        return _reference_value(value, history)

    def replace(match: re.Match) -> str:
        token = match.group(0)
        resolved = _reference_value(token, history)
        if isinstance(resolved, (dict, list, tuple, set)):
            raise ValueError("Structured workflow results must be used as an exact reference, not embedded in text")
        return str(resolved)[:10000]

    return _EMBEDDED_REF.sub(replace, value)


def _visual_request(text: str) -> tuple[str, str] | None:
    lower = (text or "").lower()
    action = any(word in lower for word in ("make", "create", "generate", "design", "turn", "animate", "render"))
    if not action:
        return None
    video = any(word in lower for word in ("video", "clip", "animate", "animation", "motion", "cinematic sequence"))
    image = any(word in lower for word in ("image", "picture", "poster", "cover", "artwork", "thumbnail", "graphic", "photo"))
    if video:
        return "video", _creative_instruction(text)
    if image:
        return "image", _creative_instruction(text)
    return None


def _creative_instruction(text: str) -> str:
    value = " ".join((text or "").split())
    lower = value.lower()
    starts = []
    for marker in ("make ", "create ", "generate ", "design ", "turn ", "animate ", "render "):
        index = lower.find(marker)
        if index >= 0:
            starts.append(index)
    if starts:
        value = value[min(starts):]
    return value[:6000]


def _dimensions(text: str, kind: str) -> tuple[int, int]:
    compact = (text or "").lower().replace(" ", "")
    if "9:16" in compact or "vertical" in compact or "portrait" in compact:
        return 1080, 1920
    if "16:9" in compact or "landscape" in compact or "widescreen" in compact:
        return 1920, 1080
    if "4:5" in compact:
        return 1080, 1350
    if "1:1" in compact or "square" in compact:
        return 1024, 1024
    return (1080, 1920) if kind == "video" else (1024, 1024)


def _attachment_visual_plan(text: str, pinned_project: str | None):
    if not pinned_project or not _promotion_requested(text) or not _rights_in_member_text(text):
        return None
    visual = _visual_request(text)
    if not visual:
        return None
    attachments = latest_attachments()
    if len(attachments) != 1:
        return None
    attachment = attachments[0]
    kind, instruction = visual
    width, height = _dimensions(text, kind)
    promotion = tools.ToolCall(
        name="promote_current_attachment",
        arguments={
            "attachment": attachment.get("id"),
            "project_name": pinned_project,
            "rights_confirmed": True,
            "attestation": "The member explicitly confirmed ownership or authorization in this Aura message.",
        },
    )
    create_args: dict[str, Any] = {
        "project_name": pinned_project,
        "kind": kind,
        "prompt": instruction,
        "width": width,
        "height": height,
        "reference_ids": ["$step0.creative_reference.id"],
    }
    if kind == "video":
        create_args.update({"frames": 121, "fps": 24})
    return tools.ToolPlan(calls=[promotion, tools.ToolCall(name="create_visual", arguments=create_args)])


def install_aura_workflow_engine() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        history = getattr(self, "_aura_workflow_history", None)
        if history is None:
            history = []
            self._aura_workflow_history = history
        resolved = tools.ToolCall(
            name=call.name,
            arguments=resolve_workflow_value(dict(call.arguments or {}), history),
        )
        try:
            result = original_execute(self, resolved, latest_user_message=latest_user_message)
        except Exception as exc:
            history.append({"tool": resolved.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            raise
        history.append({"tool": resolved.name, "ok": True, "result": result})
        return result

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        visual = _attachment_visual_plan(text, pinned_project)
        if visual is not None:
            return visual
        return original_direct(text, pinned_project, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core.TOOL_PLANNER_SYSTEM += """

Sequential workflow references:
- When a later tool call genuinely depends on a value returned by an earlier planned call, its argument may use an exact reference such as $step0.creative_reference.id or $previous.directive.id.
- step numbers are zero-based in the order you return calls. Never reference a future step. Never invent a result field: use only fields that the earlier tool is documented/known to return.
- A dependent call will fail closed if the referenced earlier step fails or the field does not exist. This mechanism never bypasses normal write authorization, membership, rights or renderer checks.
"""
    _INSTALLED = True


__all__ = [
    "install_aura_workflow_engine",
    "resolve_workflow_value",
    "_dimensions",
    "_visual_request",
]
