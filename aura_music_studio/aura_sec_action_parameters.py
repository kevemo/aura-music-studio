from __future__ import annotations

import re
from typing import Any

from .aura_sec_protocol import ActionType

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

_NO_PARAMETER_ACTIONS = {
    ActionType.RUN_QUICK_SCAN,
    ActionType.RUN_FULL_SCAN,
    ActionType.REFRESH_SECURITY_STATE,
    ActionType.UPDATE_LOCAL_RULES,
    ActionType.ISOLATE_NETWORK,
    ActionType.RESTORE_NETWORK,
    ActionType.CREATE_RECOVERY_CHECKPOINT,
    ActionType.ROTATE_DEVICE_CREDENTIAL,
    ActionType.REVOKE_DEVICE,
    ActionType.REMOTE_LOCK,
    ActionType.REMOTE_WIPE,
}

_REQUIRED_TOKEN_FIELD: dict[ActionType, str] = {
    ActionType.QUARANTINE_OBJECT: "object_id",
    ActionType.RESTORE_QUARANTINED_OBJECT: "quarantine_id",
    ActionType.TERMINATE_PROCESS: "process_instance_id",
    ActionType.APPLY_VERIFIED_UPDATE: "update_id",
    ActionType.RESTORE_RECOVERY_POINT: "recovery_point_id",
    ActionType.DISABLE_STARTUP_ITEM: "startup_item_id",
    ActionType.ENABLE_STARTUP_ITEM: "startup_item_id",
    ActionType.MOVE_TO_TRASH: "file_token",
    ActionType.RESTORE_FROM_TRASH: "trash_token",
}

_DOMAIN_ACTIONS = {ActionType.BLOCK_DOMAIN, ActionType.UNBLOCK_DOMAIN}


def _validate_token(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} must be an opaque Aura Sec identifier")
    return text


def _validate_domain(value: Any) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    if not 1 <= len(domain) <= 253 or "." not in domain:
        raise ValueError("domain must be a fully qualified hostname")
    labels = domain.split(".")
    if any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("domain contains an invalid hostname label")
    # Endpoint policy deals in hostnames, not arbitrary URLs or command text.
    if "://" in domain or "/" in domain or "@" in domain or ":" in domain:
        raise ValueError("domain must not contain a URL, path, credentials or port")
    return domain


def validated_command_parameters(action: ActionType | str, details: dict | None) -> dict[str, Any]:
    """Build the only parameters allowed to cross into the privileged native protocol.

    Action `details` can contain human-readable detection context and other untrusted
    evidence. None of that is copied automatically. Only the nested `command_parameters`
    object is considered, and every action has an exact schema with unknown fields
    rejected. There is deliberately no arbitrary path, shell, PowerShell, script or URL
    parameter.
    """
    action_type = action if isinstance(action, ActionType) else ActionType(str(action))
    source = details or {}
    raw = source.get("command_parameters", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("command_parameters must be an object")

    if action_type in _NO_PARAMETER_ACTIONS:
        if raw:
            raise ValueError(f"{action_type.value} does not accept command parameters")
        return {}

    if action_type in _DOMAIN_ACTIONS:
        if set(raw) != {"domain"}:
            raise ValueError(f"{action_type.value} accepts only the domain parameter")
        return {"domain": _validate_domain(raw.get("domain"))}

    field = _REQUIRED_TOKEN_FIELD.get(action_type)
    if field:
        if set(raw) != {field}:
            raise ValueError(f"{action_type.value} requires only the {field} parameter")
        return {field: _validate_token(raw.get(field), field)}

    raise ValueError("Aura Sec action has no registered native parameter contract")


__all__ = ["validated_command_parameters"]
