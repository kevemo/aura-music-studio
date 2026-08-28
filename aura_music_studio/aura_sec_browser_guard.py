from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_ALLOWED_SOURCES = {"navigation", "link", "qr", "email", "download_redirect"}
_ALLOWED_REPUTATION_VERDICTS = {"benign", "suspicious", "scam", "phishing", "malicious"}
_DANGEROUS_SCHEMES = {"javascript", "data", "vbscript"}
_REMOTE_SCHEMES = {"http", "https"}
_TRACKING_EXACT = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "twclid",
    "ttclid",
    "vero_id",
    "_hsenc",
    "_hsmi",
}
_LURE_TOKENS = {
    "account",
    "billing",
    "invoice",
    "login",
    "password",
    "payment",
    "recover",
    "secure",
    "security",
    "signin",
    "support",
    "verify",
    "wallet",
}
_MAX_URL_LENGTH = 8192
# URL reputation is intentionally short-lived. This matches Aura Sec's threat-intelligence
# freshness policy for URL_REPUTATION rather than allowing old verdicts to remain authoritative.
_MAX_REPUTATION_LIFETIME = timedelta(hours=1)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Browser Guard timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_host(value: str) -> tuple[str, bool, ipaddress._BaseAddress | None]:
    host = (value or "").strip().rstrip(".").lower()
    if not host:
        raise ValueError("Aura Sec Browser Guard requires a hostname")
    if "%" in host:
        raise ValueError("Percent-encoded hostnames are rejected")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        return address.compressed.lower(), False, address

    had_unicode = any(ord(ch) > 127 for ch in host)
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Hostname IDNA conversion failed") from exc

    if len(ascii_host) > 253:
        raise ValueError("Hostname exceeds DNS length limits")
    labels = ascii_host.split(".")
    if any(not label or len(label) > 63 for label in labels):
        raise ValueError("Hostname contains an invalid DNS label")
    for label in labels:
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("Hostname label cannot start or end with a hyphen")
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise ValueError("Hostname contains unsupported characters")
    return ascii_host, had_unicode, None


def _normalize_policy_host(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Browser Guard policy host cannot be empty")
    if "://" in raw:
        parsed = urlsplit(raw)
        raw = parsed.hostname or ""
    host, _unicode, _address = _canonical_host(raw)
    return host


@dataclass(frozen=True)
class BrowserLocalPolicy:
    """Member/device-local URL overrides.

    Rules are exact-host only by design. Wildcards and substring matching are omitted so
    a rule for `example.com` cannot accidentally trust or block `example.com.attacker.tld`.
    """

    trusted_hosts: frozenset[str] = field(default_factory=frozenset)
    blocked_hosts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        *,
        trusted_hosts: set[str] | frozenset[str] | None = None,
        blocked_hosts: set[str] | frozenset[str] | None = None,
    ) -> "BrowserLocalPolicy":
        trusted = frozenset(_normalize_policy_host(item) for item in (trusted_hosts or set()))
        blocked = frozenset(_normalize_policy_host(item) for item in (blocked_hosts or set()))
        overlap = trusted & blocked
        if overlap:
            raise ValueError(f"Browser Guard host cannot be both trusted and blocked: {sorted(overlap)[0]}")
        return cls(trusted_hosts=trusted, blocked_hosts=blocked)


@dataclass(frozen=True)
class UrlSignal:
    code: str
    score: int
    detail: str


@dataclass(frozen=True)
class ReputationVerificationContext:
    normalized_url: str
    host: str
    source: str

    def evidence_payload(
        self,
        *,
        indicator_type: str,
        indicator: str,
        verdict: str,
        confidence: float,
        provider_id: str,
        observed_at: datetime,
        expires_at: datetime,
    ) -> bytes:
        body = {
            "schema": "aura-sec-browser-reputation-v1",
            "normalized_url": self.normalized_url,
            "host": self.host,
            "source": self.source,
            "indicator_type": indicator_type,
            "indicator": indicator,
            "verdict": verdict,
            "confidence": round(float(confidence), 6),
            "provider_id": provider_id,
            "observed_at": _iso(observed_at),
            "expires_at": _iso(expires_at),
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class VerifiedReputationEvidence:
    indicator_type: Literal["url", "host"]
    indicator: str
    verdict: Literal["benign", "suspicious", "scam", "phishing", "malicious"]
    confidence: float
    provider_id: str
    observed_at: datetime
    expires_at: datetime
    evidence_digest: str


ReputationVerifier = Callable[
    [dict, ReputationVerificationContext], VerifiedReputationEvidence | None
]


@dataclass(frozen=True)
class BrowserGuardDecision:
    source: str
    original_scheme: str
    normalized_url: str | None
    privacy_clean_url: str | None
    host: str | None
    verdict: Literal["allow", "warn", "block"]
    risk_score: int
    signals: tuple[UrlSignal, ...]
    reputation: dict | None
    tracking_parameters_removed: tuple[str, ...]
    credentials_redacted: bool
    hard_block_reason: str | None = None

    @property
    def reasons(self) -> list[str]:
        return [item.detail for item in self.signals]


def strip_tracking_parameters(url: str) -> tuple[str, tuple[str, ...]]:
    """Remove a conservative set of known click/marketing tracking parameters.

    Unknown query parameters are preserved because Browser Guard must not silently break
    application semantics. Parameter order and duplicate values are retained.
    """

    parsed = urlsplit(url)
    kept: list[tuple[str, str]] = []
    removed: list[str] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_EXACT:
            removed.append(key)
            continue
        kept.append((key, value))
    cleaned = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(kept, doseq=True),
            parsed.fragment,
        )
    )
    return cleaned, tuple(removed)


def _normalized_remote_url(raw_url: str) -> tuple[str, str, str, bool, bool, ipaddress._BaseAddress | None, int | None]:
    value = (raw_url or "").strip()
    if not value:
        raise ValueError("Aura Sec Browser Guard requires a URL")
    if len(value) > _MAX_URL_LENGTH:
        raise ValueError("URL exceeds Browser Guard length limit")
    if _CONTROL_OR_SPACE.search(value):
        raise ValueError("URL contains control characters or whitespace")
    if "\\" in value:
        raise ValueError("Backslashes are rejected in remote URLs to prevent parser ambiguity")

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ValueError("Browser Guard requires an explicit URL scheme")
    if scheme in _DANGEROUS_SCHEMES:
        return scheme, "", value, False, False, None, None
    if scheme not in _REMOTE_SCHEMES:
        raise ValueError(f"Unsupported Browser Guard URL scheme: {scheme}")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Remote URL must include a hostname")

    host, had_unicode, address = _canonical_host(parsed.hostname)
    credentials = parsed.username is not None or parsed.password is not None
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc

    default_port = (scheme == "https" and port in {None, 443}) or (scheme == "http" and port in {None, 80})
    host_for_netloc = f"[{host}]" if address is not None and address.version == 6 else host
    netloc = host_for_netloc if default_port else f"{host_for_netloc}:{port}"
    normalized = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))
    return scheme, host, normalized, credentials, had_unicode, address, port


def _local_signals(
    *,
    raw_url: str,
    scheme: str,
    host: str,
    normalized_url: str,
    credentials: bool,
    had_unicode: bool,
    address: ipaddress._BaseAddress | None,
    port: int | None,
) -> list[UrlSignal]:
    signals: list[UrlSignal] = []
    if scheme == "http":
        signals.append(UrlSignal("unencrypted_http", 15, "The destination uses unencrypted HTTP rather than HTTPS."))
    if credentials:
        signals.append(
            UrlSignal(
                "embedded_credentials",
                35,
                "The link embeds username/password-style user information; Browser Guard redacted it from the normalized URL.",
            )
        )
    if had_unicode:
        signals.append(
            UrlSignal(
                "internationalized_hostname",
                12,
                "The hostname contains internationalized characters and was normalized to its ASCII IDNA form for comparison.",
            )
        )
    if any(label.startswith("xn--") for label in host.split(".")):
        signals.append(
            UrlSignal(
                "punycode_hostname",
                18,
                "The hostname contains Punycode; visually confusable internationalized domains deserve extra scrutiny.",
            )
        )
    if address is not None:
        if address.is_private or address.is_loopback or address.is_link_local:
            signals.append(UrlSignal("local_ip_destination", 5, "The destination is a local/private IP address."))
        else:
            signals.append(
                UrlSignal(
                    "public_ip_destination",
                    24,
                    "The link targets a public IP address directly instead of a DNS hostname.",
                )
            )
    else:
        labels = host.split(".")
        if len(labels) >= 6:
            signals.append(
                UrlSignal(
                    "excessive_subdomains",
                    min(18, 6 + (len(labels) - 6) * 2),
                    "The hostname uses an unusually deep subdomain chain that can obscure the registrable destination.",
                )
            )
        if len(host) >= 100:
            signals.append(UrlSignal("very_long_hostname", 10, "The hostname is unusually long."))
        if host.count("-") >= 6:
            signals.append(UrlSignal("hyphen_heavy_hostname", 8, "The hostname contains an unusually high number of hyphens."))

    if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        signals.append(UrlSignal("nonstandard_port", 6, "The link uses a non-default network port."))

    parsed = urlsplit(normalized_url)
    searchable = f"{parsed.path}?{parsed.query}".lower()
    lure_hits = sorted({token for token in _LURE_TOKENS if token in searchable})
    if lure_hits:
        signals.append(
            UrlSignal(
                "credential_or_payment_lure_terms",
                min(12, 4 + len(lure_hits) * 2),
                "The path/query contains account, credential, support or payment lure terms; this is contextual risk only, not proof of phishing.",
            )
        )

    if raw_url.count("%25") >= 2 or raw_url.lower().count("%2f") >= 3:
        signals.append(
            UrlSignal(
                "heavy_url_encoding",
                8,
                "The URL uses unusually dense percent-encoding that can make destinations harder to inspect.",
            )
        )
    return signals


def _verify_reputation(
    *,
    context: ReputationVerificationContext,
    payload: dict,
    verifier: ReputationVerifier | None,
    now: datetime,
) -> dict:
    if verifier is None:
        raise PermissionError("A trusted Aura Sec Browser Guard reputation verifier is required")
    try:
        proof = verifier(dict(payload or {}), context)
    except Exception as exc:
        raise PermissionError("Aura Sec Browser Guard reputation verifier failed closed") from exc
    if not isinstance(proof, VerifiedReputationEvidence):
        raise PermissionError("Aura Sec Browser Guard reputation evidence was not verified")

    indicator_type = (proof.indicator_type or "").strip().lower()
    verdict = (proof.verdict or "").strip().lower()
    provider_id = (proof.provider_id or "").strip()
    if indicator_type not in {"url", "host"}:
        raise PermissionError("Unsupported Browser Guard reputation indicator type")
    if verdict not in _ALLOWED_REPUTATION_VERDICTS:
        raise PermissionError("Unsupported Browser Guard reputation verdict")
    if not provider_id or len(provider_id) > 160:
        raise PermissionError("Trusted Browser Guard reputation provider identity is required")

    try:
        confidence = float(proof.confidence)
    except (TypeError, ValueError) as exc:
        raise PermissionError("Browser Guard reputation confidence is invalid") from exc
    if not 0.0 <= confidence <= 1.0:
        raise PermissionError("Browser Guard reputation confidence must be between zero and one")

    observed = _utc(proof.observed_at)
    expires = _utc(proof.expires_at)
    current = _utc(now)
    if expires <= observed or expires - observed > _MAX_REPUTATION_LIFETIME:
        raise PermissionError("Browser Guard reputation evidence lifetime is invalid")
    if observed > current + timedelta(minutes=5):
        raise PermissionError("Browser Guard reputation evidence is issued too far in the future")
    if current >= expires:
        raise PermissionError("Browser Guard reputation evidence has expired")

    if indicator_type == "host":
        indicator, _unicode, _address = _canonical_host(proof.indicator)
        expected_indicator = context.host
    else:
        indicator = (proof.indicator or "").strip()
        expected_indicator = context.normalized_url
    if indicator != expected_indicator:
        raise PermissionError("Verified Browser Guard reputation indicator does not match the inspected destination")

    expected_digest = _sha256(
        context.evidence_payload(
            indicator_type=indicator_type,
            indicator=indicator,
            verdict=verdict,
            confidence=confidence,
            provider_id=provider_id,
            observed_at=observed,
            expires_at=expires,
        )
    )
    evidence_digest = (proof.evidence_digest or "").strip().lower()
    if not _HEX_256.fullmatch(evidence_digest) or evidence_digest != expected_digest:
        raise PermissionError("Verified Browser Guard reputation digest does not match canonical evidence")

    return {
        "indicator_type": indicator_type,
        "indicator": indicator,
        "verdict": verdict,
        "confidence": confidence,
        "provider_id": provider_id,
        "observed_at": _iso(observed),
        "expires_at": _iso(expires),
        "evidence_digest": evidence_digest,
    }


def evaluate_url(
    raw_url: str,
    *,
    source: str = "navigation",
    local_policy: BrowserLocalPolicy | None = None,
    reputation_payload: dict | None = None,
    reputation_verifier: ReputationVerifier | None = None,
    now: datetime | None = None,
) -> BrowserGuardDecision:
    """Evaluate one destination without pretending heuristics are malware proof.

    Pure URL heuristics can produce `warn`, not a threat-intelligence `block`. A block is
    reserved for dangerous non-web executable schemes, an explicit member/device block
    rule, or fresh independently verified malicious/phishing/scam reputation evidence.
    """

    source_value = (source or "").strip().lower()
    if source_value not in _ALLOWED_SOURCES:
        raise ValueError("Unsupported Browser Guard navigation source")

    stripped = (raw_url or "").strip()
    if not stripped:
        raise ValueError("Aura Sec Browser Guard requires a URL")
    if len(stripped) > _MAX_URL_LENGTH:
        raise ValueError("URL exceeds Browser Guard length limit")
    parsed_initial = urlsplit(stripped)
    initial_scheme = parsed_initial.scheme.lower()
    if initial_scheme in _DANGEROUS_SCHEMES:
        signal = UrlSignal(
            "dangerous_executable_scheme",
            100,
            f"The {initial_scheme}: scheme can execute or embed active content and is blocked by policy.",
        )
        return BrowserGuardDecision(
            source=source_value,
            original_scheme=initial_scheme,
            normalized_url=None,
            privacy_clean_url=None,
            host=None,
            verdict="block",
            risk_score=100,
            signals=(signal,),
            reputation=None,
            tracking_parameters_removed=(),
            credentials_redacted=False,
            hard_block_reason="dangerous_scheme",
        )

    scheme, host, normalized, credentials, had_unicode, address, port = _normalized_remote_url(stripped)
    clean_url, removed = strip_tracking_parameters(normalized)
    signals = _local_signals(
        raw_url=stripped,
        scheme=scheme,
        host=host,
        normalized_url=normalized,
        credentials=credentials,
        had_unicode=had_unicode,
        address=address,
        port=port,
    )
    score = min(100, sum(item.score for item in signals))
    verdict: Literal["allow", "warn", "block"] = "warn" if score >= 20 else "allow"
    hard_block_reason: str | None = None

    policy = local_policy or BrowserLocalPolicy()
    if host in policy.blocked_hosts:
        signals.append(UrlSignal("local_block_rule", 100, "This exact hostname is blocked by the member/device Browser Guard policy."))
        score = 100
        verdict = "block"
        hard_block_reason = "local_policy"
    elif host in policy.trusted_hosts:
        signals.append(
            UrlSignal(
                "local_trust_rule",
                0,
                "This exact hostname is locally trusted, but verified threat intelligence can still override that trust rule.",
            )
        )

    reputation: dict | None = None
    if reputation_payload is not None or reputation_verifier is not None:
        context = ReputationVerificationContext(normalized_url=normalized, host=host, source=source_value)
        reputation = _verify_reputation(
            context=context,
            payload=reputation_payload or {},
            verifier=reputation_verifier,
            now=(now or datetime.now(timezone.utc)),
        )
        rep_verdict = reputation["verdict"]
        confidence = float(reputation["confidence"])
        if rep_verdict in {"malicious", "phishing", "scam"} and confidence >= 0.70:
            signals.append(
                UrlSignal(
                    "verified_threat_reputation",
                    max(90, round(confidence * 100)),
                    f"Fresh verified reputation identifies this destination as {rep_verdict} with confidence {confidence:.2f}.",
                )
            )
            score = max(score, max(90, round(confidence * 100)))
            verdict = "block"
            hard_block_reason = "verified_reputation"
        elif rep_verdict in {"malicious", "phishing", "scam", "suspicious"} and confidence >= 0.40:
            signals.append(
                UrlSignal(
                    "verified_suspicious_reputation",
                    max(35, round(confidence * 70)),
                    f"Fresh verified reputation marks this destination {rep_verdict}; Browser Guard requires caution.",
                )
            )
            score = max(score, max(35, round(confidence * 70)))
            if verdict != "block":
                verdict = "warn"
        elif rep_verdict == "benign":
            signals.append(
                UrlSignal(
                    "verified_benign_reputation",
                    0,
                    "Fresh reputation evidence is benign, but it does not suppress independent local risk signals.",
                )
            )

    return BrowserGuardDecision(
        source=source_value,
        original_scheme=scheme,
        normalized_url=normalized,
        privacy_clean_url=clean_url,
        host=host,
        verdict=verdict,
        risk_score=min(100, score),
        signals=tuple(signals),
        reputation=reputation,
        tracking_parameters_removed=removed,
        credentials_redacted=credentials,
        hard_block_reason=hard_block_reason,
    )


__all__ = [
    "BrowserGuardDecision",
    "BrowserLocalPolicy",
    "ReputationVerificationContext",
    "ReputationVerifier",
    "UrlSignal",
    "VerifiedReputationEvidence",
    "evaluate_url",
    "strip_tracking_parameters",
]
