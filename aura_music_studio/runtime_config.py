from __future__ import annotations

import os
from typing import Mapping

from pydantic import SecretStr

from .capabilities import CapabilityRecord, derive_provider_capability
from .shared_contracts import ContractModel, NonEmptyId


_TRUE = {"1", "true", "yes", "on"}


def _flag(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE


class ProviderRuntimeConfig(ContractModel):
    provider: NonEmptyId
    capability_key: NonEmptyId
    implemented: bool = False
    configured: bool = False
    owner_enabled: bool = False
    feature_flag_enabled: bool = False
    approval_granted: bool = False
    user_eligible: bool = True
    healthy: bool = True
    degraded: bool = False
    credential: SecretStr | None = None

    def capability_state(self) -> CapabilityRecord:
        return derive_provider_capability(
            key=self.capability_key,
            provider=self.provider,
            implemented=self.implemented,
            configured=self.configured,
            owner_enabled=self.owner_enabled,
            feature_flag_enabled=self.feature_flag_enabled,
            credentials_present=bool(
                self.credential and self.credential.get_secret_value().strip()
            ),
            approval_granted=self.approval_granted,
            user_eligible=self.user_eligible,
            healthy=self.healthy,
            degraded=self.degraded,
        )

    def public_payload(self) -> dict:
        state = self.capability_state()
        return {
            "provider": self.provider,
            "capability_key": self.capability_key,
            "status": state.status.value,
            "available": state.available,
            "reason": state.reason,
        }


def provider_config_from_env(
    *,
    provider: str,
    capability_key: str,
    credential_env: str,
    prefix: str,
    implemented: bool,
    environ: Mapping[str, str] | None = None,
) -> ProviderRuntimeConfig:
    """Load provider state from server environment without exposing credential values."""

    env = os.environ if environ is None else environ
    enabled_key = f"{prefix}_ENABLED"
    feature_flag_key = f"{prefix}_FEATURE_FLAG"
    approval_key = f"{prefix}_APPROVED"
    configured = any(
        key in env
        for key in (enabled_key, feature_flag_key, approval_key, credential_env)
    )
    return ProviderRuntimeConfig(
        provider=provider,
        capability_key=capability_key,
        implemented=implemented,
        configured=configured,
        owner_enabled=_flag(env.get(enabled_key)),
        feature_flag_enabled=_flag(env.get(feature_flag_key)),
        approval_granted=_flag(env.get(approval_key)),
        credential=SecretStr(env[credential_env]) if env.get(credential_env, "").strip() else None,
    )
