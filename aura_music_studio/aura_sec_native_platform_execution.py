from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .aura_sec_command_sequence import (
    AuraSecSequencedNativeExecutionGate,
    SequencedExecutionReservation,
)
from .aura_sec_command_signing import SignedSecurityCommand
from .aura_sec_protocol import ActionType

_SUPPORTED_PLATFORMS = frozenset({"windows", "macos", "linux"})
_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_RESULT_CODE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec native platform timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _platform(value: str) -> str:
    platform = str(value or "").strip().lower()
    if platform not in _SUPPORTED_PLATFORMS:
        raise PermissionError(
            "Aura Sec privileged native execution currently requires an explicit Windows, macOS or Linux adapter"
        )
    return platform


class NativePlatformExecutionEvidence(BaseModel):
    """Bounded evidence returned by a trusted local platform adapter after one dispatch.

    The adapter never returns shell output, arbitrary paths, command lines or raw privileged
    material through this contract. A platform-specific implementation may keep richer local
    diagnostics, but the control-plane evidence is deliberately reduced to opaque identifiers,
    bounded result state and SHA-256 proof material.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    platform: str = Field(min_length=3, max_length=16)
    executor_id: str = Field(min_length=3, max_length=160)
    operation_id: str = Field(min_length=3, max_length=160)
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    command_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: ActionType
    outcome: str = Field(pattern=r"^(completed|failed)$")
    result_code: str = Field(min_length=1, max_length=100)
    started_at: datetime
    completed_at: datetime
    native_proof_digest: str = Field(min_length=64, max_length=64)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _platform(value)

    @field_validator("executor_id")
    @classmethod
    def validate_executor_id(cls, value: str) -> str:
        if not _EXECUTOR_ID.fullmatch(value):
            raise ValueError("Aura Sec native executor identity is invalid")
        return value

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not _OPERATION_ID.fullmatch(value):
            raise ValueError("Aura Sec native operation identity is invalid")
        return value

    @field_validator("result_code")
    @classmethod
    def validate_result_code(cls, value: str) -> str:
        if not _RESULT_CODE.fullmatch(value):
            raise ValueError("Aura Sec native result code is invalid")
        return value

    @field_validator("native_proof_digest")
    @classmethod
    def validate_native_proof_digest(cls, value: str) -> str:
        digest = str(value or "").strip().lower()
        if not _HEX_256.fullmatch(digest):
            raise ValueError("Aura Sec native proof digest must be lowercase SHA-256 hex")
        return digest

    @field_validator("started_at", "completed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_execution_window(self):
        if self.completed_at < self.started_at:
            raise ValueError("Aura Sec native completion cannot predate execution start")
        if (self.completed_at - self.started_at) > timedelta(hours=1):
            raise ValueError("Aura Sec native execution evidence exceeds the maximum one-hour window")
        return self


def canonical_native_platform_execution_evidence(
    evidence: NativePlatformExecutionEvidence,
) -> bytes:
    """Canonical domain-separated evidence bytes used for durable completion binding."""
    if not isinstance(evidence, NativePlatformExecutionEvidence):
        evidence = NativePlatformExecutionEvidence.model_validate(evidence)
    payload = evidence.model_dump(mode="json")
    payload["native_proof_digest"] = evidence.native_proof_digest.lower()
    return (
        "AURA-SEC-NATIVE-PLATFORM-EXECUTION-EVIDENCE-V1\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def native_platform_execution_evidence_digest(
    evidence: NativePlatformExecutionEvidence,
) -> str:
    return hashlib.sha256(canonical_native_platform_execution_evidence(evidence)).hexdigest()


class AuraSecNativePlatformAdapter(Protocol):
    """Deployment-supplied, platform-native bounded action adapter.

    Concrete implementations may call approved OS-native APIs/services. They must expose the
    platform and exact ActionType set they implement, and must return bounded evidence. This
    protocol intentionally has no arbitrary command, argv, script, path or URL method.
    """

    platform: str
    executor_id: str
    supported_actions: frozenset[ActionType]

    def execute(self, command: SignedSecurityCommand) -> NativePlatformExecutionEvidence:
        ...


@dataclass(frozen=True)
class NativePlatformDispatchResult:
    command_id: str
    device_id: str
    platform: str
    action: str
    state: str
    executed: bool
    duplicate: bool
    evidence_digest: str | None
    reservation: SequencedExecutionReservation
    evidence: NativePlatformExecutionEvidence | None


class AuraSecNativePlatformExecutor:
    """Required bridge from trusted Aura Sec command admission to a native platform adapter.

    Ordering is security-critical:
      1. resolve an explicitly registered platform adapter and frozen action capability;
      2. verify server signature + per-device anti-rollback sequence;
      3. durably reserve execute-once state before any side effect;
      4. call the bounded native adapter exactly once;
      5. validate returned evidence against the exact command/registered identity/platform;
      6. persist terminal completion/failure evidence.

    Adapter identity and supported-action declarations are snapshotted at registration time so
    later mutation cannot widen authority. Duplicate or crash-window redelivery never calls the
    native adapter again. There is no generic fallback executor and no shell/script/binary
    execution surface in this module.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        adapters: Mapping[str, AuraSecNativePlatformAdapter],
        gate: AuraSecSequencedNativeExecutionGate | None = None,
    ):
        self.gate = gate or AuraSecSequencedNativeExecutionGate(db_path)
        self.adapters: dict[str, AuraSecNativePlatformAdapter] = {}
        self._executor_ids: dict[str, str] = {}
        self._supported_actions: dict[str, frozenset[ActionType]] = {}
        for key, adapter in dict(adapters or {}).items():
            platform = _platform(key)
            adapter_platform = _platform(getattr(adapter, "platform", ""))
            if adapter_platform != platform:
                raise ValueError("Aura Sec native adapter platform does not match registry key")
            executor_id = str(getattr(adapter, "executor_id", "") or "").strip()
            if not _EXECUTOR_ID.fullmatch(executor_id):
                raise ValueError("Aura Sec native adapter requires a bounded executor identity")
            raw_actions = getattr(adapter, "supported_actions", None)
            if not isinstance(raw_actions, frozenset) or not raw_actions:
                raise ValueError("Aura Sec native adapter must declare a non-empty frozen action set")
            actions: set[ActionType] = set()
            try:
                for action in raw_actions:
                    actions.add(action if isinstance(action, ActionType) else ActionType(str(action)))
            except (TypeError, ValueError) as exc:
                raise ValueError("Aura Sec native adapter declared an unsupported action") from exc
            if len(actions) != len(raw_actions):
                raise ValueError("Aura Sec native adapter action declaration is ambiguous")
            self.adapters[platform] = adapter
            self._executor_ids[platform] = executor_id
            self._supported_actions[platform] = frozenset(actions)

    @staticmethod
    def _failure(
        gate: AuraSecSequencedNativeExecutionGate,
        *,
        command: SignedSecurityCommand,
        code: str,
        now: datetime | None,
    ) -> None:
        gate.executions.mark_failed(
            device_id=command.device_id,
            command_id=command.command_id,
            failure_code=code,
            now=now,
        )

    @staticmethod
    def _validate_evidence_binding(
        evidence: NativePlatformExecutionEvidence,
        *,
        expected_executor_id: str,
        command: SignedSecurityCommand,
        platform: str,
        validation_time: datetime,
    ) -> None:
        if evidence.platform != platform:
            raise PermissionError("Aura Sec native evidence returned the wrong platform")
        if evidence.executor_id != expected_executor_id:
            raise PermissionError("Aura Sec native evidence returned the wrong executor identity")
        if evidence.device_id != command.device_id:
            raise PermissionError("Aura Sec native evidence returned the wrong device identity")
        if evidence.command_id != command.command_id:
            raise PermissionError("Aura Sec native evidence returned the wrong command identity")
        if evidence.action != command.action:
            raise PermissionError("Aura Sec native evidence returned the wrong action")
        if evidence.started_at < command.issued_at - timedelta(seconds=30):
            raise PermissionError("Aura Sec native evidence starts before command issuance")
        if evidence.started_at > command.expires_at:
            raise PermissionError("Aura Sec native execution did not start before command expiry")
        if evidence.completed_at > validation_time + timedelta(seconds=30):
            raise PermissionError("Aura Sec native evidence completion is too far in the future")

    def dispatch(
        self,
        command: SignedSecurityCommand,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        platform: str,
        now: datetime | None = None,
    ) -> NativePlatformDispatchResult:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)
        selected_platform = _platform(platform)
        adapter = self.adapters.get(selected_platform)
        if adapter is None:
            raise PermissionError(
                f"No trusted Aura Sec native adapter is registered for {selected_platform}"
            )
        executor_id = self._executor_ids[selected_platform]
        supported_actions = self._supported_actions[selected_platform]
        if command.action not in supported_actions:
            raise PermissionError(
                f"Aura Sec native adapter {executor_id} does not implement {command.action.value}"
            )

        reservation = self.gate.reserve_verified_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        if not reservation.execution.execute:
            record = self.gate.executions.get(
                device_id=command.device_id,
                command_id=command.command_id,
            )
            return NativePlatformDispatchResult(
                command_id=command.command_id,
                device_id=command.device_id,
                platform=selected_platform,
                action=command.action.value,
                state=str(record["state"]),
                executed=False,
                duplicate=True,
                evidence_digest=record.get("result_evidence_digest"),
                reservation=reservation,
                evidence=None,
            )

        try:
            raw_evidence = adapter.execute(command)
        except Exception as exc:
            self._failure(
                self.gate,
                command=command,
                code="platform_executor_exception",
                now=now,
            )
            raise RuntimeError("Aura Sec native platform executor failed closed") from exc

        try:
            evidence = (
                raw_evidence
                if isinstance(raw_evidence, NativePlatformExecutionEvidence)
                else NativePlatformExecutionEvidence.model_validate(raw_evidence)
            )
            validation_time = _utc(now) if now is not None else _utc()
            self._validate_evidence_binding(
                evidence,
                expected_executor_id=executor_id,
                command=command,
                platform=selected_platform,
                validation_time=validation_time,
            )
        except Exception as exc:
            self._failure(
                self.gate,
                command=command,
                code="invalid_platform_evidence",
                now=now,
            )
            raise PermissionError("Aura Sec native platform evidence failed closed") from exc

        evidence_digest = native_platform_execution_evidence_digest(evidence)
        if evidence.outcome == "completed":
            record = self.gate.executions.mark_completed(
                device_id=command.device_id,
                command_id=command.command_id,
                result_evidence_digest=evidence_digest,
                now=evidence.completed_at,
            )
        else:
            record = self.gate.executions.mark_failed(
                device_id=command.device_id,
                command_id=command.command_id,
                failure_code=evidence.result_code,
                now=evidence.completed_at,
            )

        return NativePlatformDispatchResult(
            command_id=command.command_id,
            device_id=command.device_id,
            platform=selected_platform,
            action=command.action.value,
            state=str(record["state"]),
            executed=True,
            duplicate=False,
            evidence_digest=evidence_digest,
            reservation=reservation,
            evidence=evidence,
        )


__all__ = [
    "AuraSecNativePlatformAdapter",
    "AuraSecNativePlatformExecutor",
    "NativePlatformDispatchResult",
    "NativePlatformExecutionEvidence",
    "canonical_native_platform_execution_evidence",
    "native_platform_execution_evidence_digest",
]
