from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .aura_sec_dlp import redact_text, sanitize_audit_details
from .shared_contracts import ContractModel, NonEmptyId


class ApiErrorCode(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    VALIDATION_FAILED = "validation_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    STALE_VERSION = "stale_version"
    NOT_FOUND = "not_found"
    CONFIRMATION_REQUIRED = "confirmation_required"
    INTERNAL_ERROR = "internal_error"


HTTP_STATUS_BY_CODE = {
    ApiErrorCode.UNAUTHENTICATED: 401,
    ApiErrorCode.FORBIDDEN: 403,
    ApiErrorCode.VALIDATION_FAILED: 422,
    ApiErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ApiErrorCode.RATE_LIMITED: 429,
    ApiErrorCode.PROVIDER_UNAVAILABLE: 503,
    ApiErrorCode.CAPABILITY_UNAVAILABLE: 503,
    ApiErrorCode.STALE_VERSION: 409,
    ApiErrorCode.NOT_FOUND: 404,
    ApiErrorCode.CONFIRMATION_REQUIRED: 409,
    ApiErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(ContractModel):
    code: ApiErrorCode
    message: str = Field(min_length=1, max_length=1000)
    correlation_id: NonEmptyId
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_CODE[self.code]

    def public_payload(self) -> dict[str, Any]:
        """Return a bounded client-safe error body without secrets or stack details."""

        payload = self.model_dump(mode="json")
        if self.code is ApiErrorCode.INTERNAL_ERROR:
            payload["message"] = "Internal error"
            payload["details"] = {}
            return payload
        payload["message"] = redact_text(self.message, max_length=1000)
        payload["details"] = sanitize_audit_details(
            self.details,
            max_depth=5,
            max_items=50,
            max_string_length=1000,
        )
        return payload
