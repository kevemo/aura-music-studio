from __future__ import annotations

from typing import Any

from .aura_avatar_bridge import AuraAvatarBridge, AuraAvatarBridgeError
from .aura_companion import AuraCompanionError
from .aura_system_companion import AuraSystemCompanionService

_bridge = AuraAvatarBridge()
_ORIGINAL_DEFINITIONS = AuraSystemCompanionService._tool_definitions
_ORIGINAL_EXECUTE = AuraSystemCompanionService._execute_tool
_ORIGINAL_PROMPT = AuraSystemCompanionService._system_prompt
_INSTALLED = False


def _tool_definitions(self, member) -> list[dict[str, Any]]:
    tools = list(_ORIGINAL_DEFINITIONS(self, member))
    if not tools:
        return tools
    tools.extend(
        [
            {
                "type": "function",
                "name": "get_current_interface",
                "description": (
                    "Inspect the signed-in user's currently open Live Sound Studio/ESP screen and return the bounded list of visible "
                    "controls that embodied Aura is allowed to point to. Use this before guide_interface when you need to show the user "
                    "where a feature is."
                ),
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            },
            {
                "type": "function",
                "name": "guide_interface",
                "description": (
                    "Command the user's embodied Aura character to guide, present, celebrate, listen, think, minimise or restore. "
                    "For guide_to, control_id must come from get_current_interface. Never invent a control_id."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["guide_to", "present", "celebrate", "minimize", "restore", "listen", "think"],
                        },
                        "control_id": {"type": ["string", "null"]},
                        "message": {"type": "string"},
                        "speak": {"type": "boolean"},
                    },
                    "required": ["action", "control_id", "message", "speak"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    return tools


def _system_prompt(self, member, *, project_context: dict | None = None) -> str:
    base = _ORIGINAL_PROMPT(self, member, project_context=project_context)
    return base + (
        " You have an embodied 3D interface presence when the user is on a supported signed-in screen. "
        "When the user asks where a feature/control is, asks you to show them, guide them, point something out, or asks what a visible "
        "control does, prefer using get_current_interface and then guide_interface rather than giving text-only navigation instructions. "
        "Never invent control IDs and never claim you pointed to something unless guide_interface accepted the command. "
        "Use brief spoken guidance while pointing. For successful creative completions, you may use the celebrate action sparingly. "
        "Use think/listen/present only when it materially improves the interaction; avoid constant animation that would distract the user."
    )


def _execute_tool(self, member, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "get_current_interface":
        context = _bridge.page_context(member.user_id)
        return {
            "path": context.get("path"),
            "title": context.get("title"),
            "updated_at": context.get("updated_at"),
            "controls": [
                {"id": item.get("id"), "label": item.get("label"), "kind": item.get("kind")}
                for item in context.get("controls", [])
            ],
            "note": "Only these current-screen control IDs may be passed to guide_interface.",
        }

    if name == "guide_interface":
        try:
            return _bridge.enqueue(
                member.user_id,
                action=str(arguments.get("action") or ""),
                control_id=arguments.get("control_id"),
                message=str(arguments.get("message") or ""),
                speak=bool(arguments.get("speak", True)),
            )
        except AuraAvatarBridgeError as exc:
            raise AuraCompanionError(str(exc)) from exc

    return _ORIGINAL_EXECUTE(self, member, name, arguments)


def install_embodied_aura_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    AuraSystemCompanionService._tool_definitions = _tool_definitions
    AuraSystemCompanionService._system_prompt = _system_prompt
    AuraSystemCompanionService._execute_tool = _execute_tool
    _INSTALLED = True


install_embodied_aura_tools()
