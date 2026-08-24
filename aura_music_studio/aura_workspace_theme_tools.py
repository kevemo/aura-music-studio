from __future__ import annotations

from typing import Any

from .aura_companion import AuraCompanionError
from .aura_system_companion import AuraSystemCompanionService
from .workspace_theme import themes

_ORIGINAL_DEFINITIONS = AuraSystemCompanionService._tool_definitions
_ORIGINAL_EXECUTE = AuraSystemCompanionService._execute_tool
_ORIGINAL_PROMPT = AuraSystemCompanionService._system_prompt
_INSTALLED = False


def _subject(member) -> str:
    return f"member:{member.user_id}"


def _tool_definitions(self, member) -> list[dict[str, Any]]:
    tools = list(_ORIGINAL_DEFINITIONS(self, member))
    if not tools:
        return tools
    colour = {"type": ["string", "null"], "pattern": "^#[0-9A-Fa-f]{6}$"}
    tools.extend(
        [
            {
                "type": "function",
                "name": "get_workspace_theme",
                "description": "Read the signed-in user's currently saved 4Infinity Creative Studios workspace theme.",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            },
            {
                "type": "function",
                "name": "preview_workspace_theme",
                "description": (
                    "Create a safe temporary preview of requested workspace colours/design tokens. "
                    "This does NOT save the change. Always show/explain the preview and wait for explicit user acceptance before confirm_workspace_theme."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "background": colour,
                        "surface": colour,
                        "surface_alt": colour,
                        "accent": colour,
                        "accent_alt": colour,
                        "text": colour,
                        "muted": colour,
                        "radius_px": {"type": ["integer", "null"], "minimum": 0, "maximum": 40},
                        "font_scale": {"type": ["number", "null"], "minimum": 0.85, "maximum": 1.3},
                        "density": {"type": ["string", "null"], "enum": ["compact", "comfortable", "spacious", None]},
                        "motion": {"type": ["string", "null"], "enum": ["reduced", "balanced", "full", None]},
                        "background_style": {"type": ["string", "null"], "enum": ["solid", "gradient", "cosmic", "minimal", None]},
                        "font_style": {"type": ["string", "null"], "enum": ["system", "rounded", "serif", "mono", None]},
                        "aura_glow": {"type": ["string", "null"], "enum": ["subtle", "balanced", "radiant", None]},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "background", "surface", "surface_alt", "accent", "accent_alt", "text", "muted",
                        "radius_px", "font_scale", "density", "motion", "background_style", "font_style", "aura_glow", "reason"
                    ],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "confirm_workspace_theme",
                "description": "Save a previously created theme preview only after the user explicitly accepts it.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"preview_id": {"type": "string"}},
                    "required": ["preview_id"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "discard_workspace_theme_preview",
                "description": "Discard a pending theme preview when the user rejects it. The saved theme remains unchanged.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"preview_id": {"type": "string"}},
                    "required": ["preview_id"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "revert_workspace_theme",
                "description": "Revert the signed-in user's last saved workspace theme change when they explicitly ask to undo/revert it.",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            },
        ]
    )
    return tools


def _system_prompt(self, member, *, project_context: dict | None = None) -> str:
    base = _ORIGINAL_PROMPT(self, member, project_context=project_context)
    return base + (
        " You can personalize the signed-in user's 4Infinity Creative Studios workspace through safe theme tokens. "
        "When asked to change colours, layout feel, corner style, font feel, spacing, motion or Aura glow, use preview_workspace_theme first. "
        "A preview is not acceptance: describe what changed and ask the user whether to keep it. Only call confirm_workspace_theme after explicit acceptance. "
        "If they reject it, use discard_workspace_theme_preview. If they ask to undo the last saved design, use revert_workspace_theme. "
        "Never claim arbitrary CSS or JavaScript was installed; the theme engine intentionally allows only validated design tokens."
    )


def _execute_tool(self, member, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    subject = _subject(member)
    actor = f"Aura for member {member.user_id}"

    if name == "get_workspace_theme":
        current = themes.current(subject)
        return {**current, "subject": "member"}

    if name == "preview_workspace_theme":
        changes = {key: value for key, value in arguments.items() if key != "reason" and value is not None}
        try:
            return themes.create_preview(subject, changes, str(arguments.get("reason") or "Aura workspace customization"))
        except ValueError as exc:
            raise AuraCompanionError(str(exc)) from exc

    if name == "confirm_workspace_theme":
        try:
            return themes.confirm(subject, str(arguments.get("preview_id") or ""), actor)
        except ValueError as exc:
            raise AuraCompanionError(str(exc)) from exc

    if name == "discard_workspace_theme_preview":
        try:
            return themes.discard(subject, str(arguments.get("preview_id") or ""))
        except ValueError as exc:
            raise AuraCompanionError(str(exc)) from exc

    if name == "revert_workspace_theme":
        try:
            return themes.revert_last(subject, actor)
        except ValueError as exc:
            raise AuraCompanionError(str(exc)) from exc

    return _ORIGINAL_EXECUTE(self, member, name, arguments)


def install_workspace_theme_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    AuraSystemCompanionService._tool_definitions = _tool_definitions
    AuraSystemCompanionService._system_prompt = _system_prompt
    AuraSystemCompanionService._execute_tool = _execute_tool
    _INSTALLED = True


install_workspace_theme_tools()
