from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProtectionState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ATTENTION_REQUIRED = "attention_required"
    ISOLATED = "isolated"
    UPDATING = "updating"


class ActionRisk(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    CONFIRMATION_REQUIRED = "confirmation_required"
    STRONG_REAUTH_REQUIRED = "strong_reauth_required"


class ActionType(str, Enum):
    # Deliberately bounded. There is no arbitrary shell/PowerShell/script action.
    RUN_QUICK_SCAN = "run_quick_scan"
    RUN_FULL_SCAN = "run_full_scan"
    REFRESH_SECURITY_STATE = "refresh_security_state"
    UPDATE_LOCAL_RULES = "update_local_rules"
    QUARANTINE_OBJECT = "quarantine_object"
    RESTORE_QUARANTINED_OBJECT = "restore_quarantined_object"
    TERMINATE_PROCESS = "terminate_process"
    ISOLATE_NETWORK = "isolate_network"
    RESTORE_NETWORK = "restore_network"
    BLOCK_DOMAIN = "block_domain"
    UNBLOCK_DOMAIN = "unblock_domain"
    APPLY_VERIFIED_UPDATE = "apply_verified_update"
    CREATE_RECOVERY_CHECKPOINT = "create_recovery_checkpoint"
    RESTORE_RECOVERY_POINT = "restore_recovery_point"
    DISABLE_STARTUP_ITEM = "disable_startup_item"
    ENABLE_STARTUP_ITEM = "enable_startup_item"
    MOVE_TO_TRASH = "move_to_trash"
    RESTORE_FROM_TRASH = "restore_from_trash"
    ROTATE_DEVICE_CREDENTIAL = "rotate_device_credential"
    REVOKE_DEVICE = "revoke_device"
    REMOTE_LOCK = "remote_lock"
    REMOTE_WIPE = "remote_wipe"


EXPECTED_RISK: dict[ActionType, ActionRisk] = {
    ActionType.RUN_QUICK_SCAN: ActionRisk.LOW_RISK,
    ActionType.RUN_FULL_SCAN: ActionRisk.LOW_RISK,
    ActionType.REFRESH_SECURITY_STATE: ActionRisk.READ_ONLY,
    ActionType.UPDATE_LOCAL_RULES: ActionRisk.LOW_RISK,
    ActionType.QUARANTINE_OBJECT: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.RESTORE_QUARANTINED_OBJECT: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.TERMINATE_PROCESS: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.ISOLATE_NETWORK: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.RESTORE_NETWORK: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.BLOCK_DOMAIN: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.UNBLOCK_DOMAIN: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.APPLY_VERIFIED_UPDATE: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.CREATE_RECOVERY_CHECKPOINT: ActionRisk.LOW_RISK,
    ActionType.RESTORE_RECOVERY_POINT: ActionRisk.STRONG_REAUTH_REQUIRED,
    ActionType.DISABLE_STARTUP_ITEM: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.ENABLE_STARTUP_ITEM: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.MOVE_TO_TRASH: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.RESTORE_FROM_TRASH: ActionRisk.CONFIRMATION_REQUIRED,
    ActionType.ROTATE_DEVICE_CREDENTIAL: ActionRisk.STRONG_REAUTH_REQUIRED,
    ActionType.REVOKE_DEVICE: ActionRisk.STRONG_REAUTH_REQUIRED,
    ActionType.REMOTE_LOCK: ActionRisk.STRONG_REAUTH_REQUIRED,
    ActionType.REMOTE_WIPE: ActionRisk.STRONG_REAUTH_REQUIRED,
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeviceCapability(StrictModel):
    id: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    state: Literal["available", "degraded", "unavailable", "permission_required"]
    detail: str = Field(default="", max_length=500)


class DeviceHeartbeat(StrictModel):
    schema_version: Literal[1] = 1
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    issued_at: datetime
    expires_at: datetime
    agent_version: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)
    platform: Literal["windows", "macos", "linux", "android", "ios", "chromeos", "browser"]
    architecture: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9._+-]+$")
    protection_state: ProtectionState
    capabilities: list[DeviceCapability] = Field(default_factory=list, max_length=128)
    report_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    challenge_nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9._~-]+$")

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def heartbeat_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("heartbeat expiry must be after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 300:
            raise ValueError("heartbeat validity window cannot exceed five minutes")
        ids = [item.id for item in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("heartbeat capability ids must be unique")
        return self

    def signed_payload(self) -> bytes:
        """Canonical payload signed by the enrolled native-device key."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


class SecurityCommand(StrictModel):
    schema_version: Literal[1] = 1
    command_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: ActionType
    risk: ActionRisk
    issued_at: datetime
    expires_at: datetime
    policy_version: str = Field(min_length=1, max_length=80)
    nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9._~-]+$")
    approval_id: str | None = Field(default=None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_command_policy(self):
        expected = EXPECTED_RISK[self.action]
        if self.risk != expected:
            raise ValueError(f"{self.action.value} requires risk class {expected.value}")
        if self.expires_at <= self.issued_at:
            raise ValueError("command expiry must be after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 900:
            raise ValueError("command validity window cannot exceed fifteen minutes")
        if self.risk in {ActionRisk.CONFIRMATION_REQUIRED, ActionRisk.STRONG_REAUTH_REQUIRED} and not self.approval_id:
            raise ValueError("approved action id is required for this command")
        if self.risk in {ActionRisk.READ_ONLY, ActionRisk.LOW_RISK} and self.approval_id is not None:
            raise ValueError("approval id is not valid for an automatic low-risk command")
        if len(self.parameters) > 32:
            raise ValueError("command has too many parameters")
        for key in self.parameters:
            if not isinstance(key, str) or not 1 <= len(key) <= 80:
                raise ValueError("invalid command parameter key")

        # Defense in depth: the protocol object itself enforces the exact action schema.
        # This prevents a future caller from bypassing the parameter-firewall helper and
        # smuggling arbitrary shell, path, URL, command-line or executable fields into a
        # privileged native command.
        from .aura_sec_action_parameters import validated_command_parameters

        canonical = validated_command_parameters(
            self.action,
            {"command_parameters": self.parameters},
        )
        if canonical != self.parameters:
            raise ValueError("command parameters must already use the canonical Aura Sec action schema")
        return self


class CommandReceipt(StrictModel):
    schema_version: Literal[1] = 1
    command_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    status: Literal["received", "rejected", "executed", "failed", "verified"]
    occurred_at: datetime
    result_code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    evidence_digest: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    detail: str = Field(default="", max_length=1000)

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def verification_needs_evidence(self):
        if self.status == "verified" and not self.evidence_digest:
            raise ValueError("verified command receipt requires evidence digest")
        return self


__all__ = [
    "ActionRisk",
    "ActionType",
    "CommandReceipt",
    "DeviceCapability",
    "DeviceHeartbeat",
    "EXPECTED_RISK",
    "ProtectionState",
    "SecurityCommand",
]
