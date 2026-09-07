from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

router = APIRouter(prefix="/api/aura-os", tags=["Aura OS Contract"])

PROTOCOL_VERSION = "aura-os/1"
SurfaceKind = Literal["command_center_web", "desktop_overlay", "standalone_desktop", "mobile_companion", "live_overlay", "native_security_client"]
RiskClass = Literal["read_only", "local_ui", "confirmation", "strong_reauth", "native_internal"]


@dataclass(frozen=True, slots=True)
class AuraOsActionDefinition:
    id: str
    risk: RiskClass
    surfaces: tuple[str, ...]
    parameter_schema: dict[str, dict[str, Any]]
    description: str
    native_execution: bool = False


def _p(kind: str, *, required: bool = False, maximum_length: int | None = None, choices: tuple[str, ...] = ()) -> dict[str, Any]:
    row: dict[str, Any] = {"type": kind, "required": required}
    if maximum_length is not None:
        row["maximum_length"] = maximum_length
    if choices:
        row["choices"] = list(choices)
    return row


ACTIONS: tuple[AuraOsActionDefinition, ...] = (
    AuraOsActionDefinition("workspace.open", "local_ui", ("command_center_web", "desktop_overlay", "standalone_desktop", "mobile_companion"), {"workspace": _p("string", required=True, maximum_length=80), "project_id": _p("opaque_id", maximum_length=160)}, "Open an allowed Command Center workspace or project."),
    AuraOsActionDefinition("workspace.focus", "local_ui", ("desktop_overlay", "standalone_desktop"), {"workspace": _p("string", required=True, maximum_length=80)}, "Focus an already-open Aura/Command Center workspace."),
    AuraOsActionDefinition("overlay.show", "local_ui", ("desktop_overlay", "standalone_desktop", "live_overlay"), {"panel": _p("string", required=True, maximum_length=80)}, "Show an approved Aura overlay panel."),
    AuraOsActionDefinition("overlay.hide", "local_ui", ("desktop_overlay", "standalone_desktop", "live_overlay"), {"panel": _p("string", required=True, maximum_length=80)}, "Hide an approved Aura overlay panel."),
    AuraOsActionDefinition("overlay.theme", "local_ui", ("desktop_overlay", "standalone_desktop", "live_overlay"), {"theme_id": _p("opaque_id", required=True, maximum_length=160)}, "Apply a registered theme; no arbitrary CSS/JavaScript payload crosses the bridge."),
    AuraOsActionDefinition("notification.show", "local_ui", ("desktop_overlay", "standalone_desktop", "mobile_companion"), {"title": _p("string", required=True, maximum_length=160), "body": _p("string", required=True, maximum_length=1000)}, "Display a local Aura notification."),
    AuraOsActionDefinition("speech.speak", "local_ui", ("desktop_overlay", "standalone_desktop", "mobile_companion", "live_overlay"), {"text": _p("string", required=True, maximum_length=4000), "voice_profile_id": _p("opaque_id", maximum_length=160)}, "Speak approved text through the configured voice pipeline."),
    AuraOsActionDefinition("media.play_approved", "local_ui", ("desktop_overlay", "standalone_desktop", "live_overlay"), {"asset_id": _p("opaque_id", required=True, maximum_length=160), "channel": _p("enum", choices=("preview", "program", "alert"))}, "Play a project/library asset by opaque ID; never accept a filesystem path or URL."),
    AuraOsActionDefinition("browser.open_https", "confirmation", ("desktop_overlay", "standalone_desktop", "mobile_companion"), {"url": _p("https_url", required=True, maximum_length=2000)}, "Open an HTTPS URL after policy validation; credentials in URLs are rejected."),
    AuraOsActionDefinition("project.export", "confirmation", ("command_center_web", "desktop_overlay", "standalone_desktop"), {"project_id": _p("opaque_id", required=True, maximum_length=160), "export_profile_id": _p("opaque_id", required=True, maximum_length=160)}, "Request a registered project export profile; export remains server/project scoped."),
    AuraOsActionDefinition("workflow.approve", "strong_reauth", ("command_center_web", "desktop_overlay", "standalone_desktop", "mobile_companion"), {"approval_id": _p("opaque_id", required=True, maximum_length=160)}, "Approve an already-proposed high-impact action through the existing approval gateway."),
    AuraOsActionDefinition("aura_sec.poll_internal", "native_internal", ("native_security_client",), {"device_id": _p("opaque_id", required=True, maximum_length=160)}, "Internal Aura Sec signed native poll contract; not exposed as a general OS action.", native_execution=True),
)
ACTION_BY_ID = {row.id: row for row in ACTIONS}

FORBIDDEN_GENERIC_ACTIONS = {
    "shell", "powershell", "cmd", "terminal", "exec", "eval", "script", "run_command", "arbitrary_file", "process_spawn", "registry_edit",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Aura OS timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("Aura OS timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _safe_opaque(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(part in text for part in ("/", "\\", "..", "\x00")):
        raise ValueError("Invalid opaque identifier")
    return text


def _safe_https(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError("URL exceeds the Aura OS limit")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Aura OS external navigation accepts credential-free HTTPS URLs only")
    return text


def validate_action(action_id: str, surface: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if any(token in action_id.casefold() for token in FORBIDDEN_GENERIC_ACTIONS):
        raise ValueError("Generic command/script execution is not part of Aura OS")
    definition = ACTION_BY_ID.get(action_id)
    if definition is None:
        raise ValueError("Aura OS action is not allowlisted")
    if surface not in definition.surfaces:
        raise ValueError("Aura OS action is not allowed on this surface")
    unknown = set(parameters) - set(definition.parameter_schema)
    if unknown:
        raise ValueError(f"Unsupported Aura OS parameters: {sorted(unknown)}")
    clean: dict[str, Any] = {}
    for name, spec in definition.parameter_schema.items():
        present = name in parameters and parameters[name] not in (None, "")
        if spec.get("required") and not present:
            raise ValueError(f"Missing required Aura OS parameter: {name}")
        if not present:
            continue
        value = parameters[name]
        kind = spec["type"]
        if kind in {"string"}:
            value = str(value).strip()
            if len(value) > int(spec.get("maximum_length") or 10000):
                raise ValueError(f"Aura OS parameter is too long: {name}")
        elif kind == "opaque_id":
            value = _safe_opaque(str(value), limit=int(spec.get("maximum_length") or 160))
        elif kind == "https_url":
            value = _safe_https(str(value), limit=int(spec.get("maximum_length") or 2000))
        elif kind == "enum":
            if value not in spec.get("choices", []):
                raise ValueError(f"Unsupported Aura OS choice for {name}")
        else:
            raise ValueError(f"Unsupported Aura OS parameter schema type: {kind}")
        clean[name] = value
    return clean


class AuraOsEnvelope(BaseModel):
    protocol: str = PROTOCOL_VERSION
    request_id: str = Field(min_length=12, max_length=160)
    surface: SurfaceKind
    action_id: str = Field(min_length=3, max_length=160)
    parameters: dict[str, Any] = Field(default_factory=dict)
    issued_at: str
    expires_at: str
    actor_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    approval_evidence_id: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_contract(self):
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("Unsupported Aura OS protocol version")
        issued = _parse_time(self.issued_at)
        expires = _parse_time(self.expires_at)
        if expires <= issued:
            raise ValueError("Aura OS request expiry must be after issue time")
        if (expires - issued).total_seconds() > 120:
            raise ValueError("Aura OS request validity may not exceed two minutes")
        self.parameters = validate_action(self.action_id, self.surface, self.parameters)
        definition = ACTION_BY_ID[self.action_id]
        if definition.risk == "strong_reauth" and not self.approval_evidence_id:
            raise ValueError("Strong-reauth Aura OS action requires approval evidence")
        return self

    def canonical_payload(self) -> bytes:
        value = self.model_dump(mode="json")
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()


@dataclass(frozen=True, slots=True)
class AuraOsSurfaceDefinition:
    id: str
    description: str
    native: bool
    privileged: bool


SURFACES: tuple[AuraOsSurfaceDefinition, ...] = (
    AuraOsSurfaceDefinition("command_center_web", "Authenticated browser Command Center surface.", False, False),
    AuraOsSurfaceDefinition("desktop_overlay", "Future signed desktop overlay displaying Aura and approved project/UI actions.", True, False),
    AuraOsSurfaceDefinition("standalone_desktop", "Future standalone Aura desktop application using the same protocol.", True, False),
    AuraOsSurfaceDefinition("mobile_companion", "Future mobile companion surface for approved navigation, voice and notifications.", True, False),
    AuraOsSurfaceDefinition("live_overlay", "Browser-source/live production renderer with bounded visual/media actions.", False, False),
    AuraOsSurfaceDefinition("native_security_client", "Separate privileged Aura Sec native client boundary.", True, True),
)


def manifest() -> dict:
    return {
        "protocol": PROTOCOL_VERSION,
        "surfaces": [asdict(row) for row in SURFACES],
        "actions": [asdict(row) for row in ACTIONS],
        "security": {
            "generic_shell": False,
            "generic_powershell": False,
            "generic_script_execution": False,
            "arbitrary_filesystem_paths": False,
            "credential_bearing_urls": False,
            "max_request_validity_seconds": 120,
            "native_execution_requires_separate_signed_client": True,
            "browser_approval_is_not_native_execution": True,
        },
    }


def _require_member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


@router.get("/manifest")
def aura_os_manifest(request: Request):
    _require_member(request)
    return manifest()


@router.post("/validate")
def aura_os_validate(body: AuraOsEnvelope, request: Request):
    member = _require_member(request)
    if body.actor_id != member.user_id:
        raise HTTPException(403, "Aura OS actor must match the authenticated member")
    definition = ACTION_BY_ID[body.action_id]
    return {
        "valid": True,
        "digest": body.digest(),
        "risk": definition.risk,
        "native_execution": definition.native_execution,
        "execution_performed": False,
        "note": "Validation confirms the bounded protocol payload only; privileged native execution requires the appropriate signed native client/bridge.",
    }


__all__ = ["router", "ACTIONS", "SURFACES", "AuraOsEnvelope", "validate_action", "manifest", "PROTOCOL_VERSION"]
