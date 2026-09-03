from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_REDACTED_SECRET = "[REDACTED:SECRET]"
_REDACTED_PII = "[REDACTED:PII]"
_REDACTED_EMAIL = "[REDACTED:EMAIL]"
_REDACTED_TOKEN = "[REDACTED:TOKEN]"
_TRUNCATED = "[TRUNCATED]"

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_COMMON_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{20,})\b"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|key|secret|password|passwd|signature|sig|code)=)[^&#\s]+"
)

_SECRET_KEY_PARTS = {
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "authorization",
    "bearer",
    "cookie",
    "session",
    "csrf",
    "credential",
    "privatekey",
    "private_key",
    "apikey",
    "api_key",
    "accesskey",
    "access_key",
    "refresh",
    "idtoken",
    "id_token",
    "cvv",
    "cvc",
    "cardnumber",
    "card_number",
}

_PII_KEY_PARTS = {
    "email",
    "phone",
    "mobile",
    "address",
    "postcode",
    "postalcode",
    "postal_code",
    "dob",
    "dateofbirth",
    "date_of_birth",
    "fullname",
    "full_name",
    "firstname",
    "first_name",
    "lastname",
    "last_name",
    "displayname",
    "display_name",
}


def _normalise_key(key: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key).strip().lower())


def _classify_key(key: object) -> str | None:
    normalised = _normalise_key(key)
    compact = normalised.replace("_", "")
    if any(part.replace("_", "") in compact for part in _SECRET_KEY_PARTS):
        return "secret"
    if any(part.replace("_", "") in compact for part in _PII_KEY_PARTS):
        return "pii"
    return None


def redact_text(value: str, *, max_length: int = 4096) -> str:
    """Remove common credential and direct-identifier material from free-form audit text.

    This is a defensive last line rather than a replacement for structured logging. It is
    intentionally conservative: security logs should retain event semantics, not user data.
    """

    text = str(value)
    text = _BEARER_RE.sub(_REDACTED_TOKEN, text)
    text = _JWT_RE.sub(_REDACTED_TOKEN, text)
    text = _COMMON_SECRET_RE.sub(_REDACTED_SECRET, text)
    text = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}{_REDACTED_SECRET}", text)
    text = _EMAIL_RE.sub(_REDACTED_EMAIL, text)
    if len(text) > max_length:
        text = text[:max_length] + _TRUNCATED
    return text


def sanitize_audit_details(
    value: Any,
    *,
    max_depth: int = 8,
    max_items: int = 100,
    max_string_length: int = 4096,
    _depth: int = 0,
) -> Any:
    """Return a JSON-safe audit representation with secrets and direct PII removed.

    Keyed secret/PII fields are redacted before their values are inspected. Free-form strings
    are pattern-scrubbed as a secondary guard. Depth/item caps prevent audit payload abuse from
    becoming a storage or recursive-processing denial of service.
    """

    if _depth >= max_depth:
        return "[REDACTED:DEPTH_LIMIT]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return redact_text(value, max_length=max_string_length)

    if isinstance(value, bytes):
        return "[REDACTED:BINARY]"

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= max_items:
                result["_truncated"] = True
                break
            key = redact_text(str(raw_key), max_length=160)
            classification = _classify_key(raw_key)
            if classification == "secret":
                result[key] = _REDACTED_SECRET
            elif classification == "pii":
                result[key] = _REDACTED_PII
            else:
                result[key] = sanitize_audit_details(
                    raw_value,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_string_length=max_string_length,
                    _depth=_depth + 1,
                )
        return result

    if isinstance(value, Sequence):
        result = []
        for index, item in enumerate(value):
            if index >= max_items:
                result.append(_TRUNCATED)
                break
            result.append(
                sanitize_audit_details(
                    item,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_string_length=max_string_length,
                    _depth=_depth + 1,
                )
            )
        return result

    return redact_text(str(value), max_length=max_string_length)
