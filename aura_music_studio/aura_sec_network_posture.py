from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NetworkScope(str, Enum):
    TRUSTED_PRIVATE = "trusted_private"
    PRIVATE = "private"
    PUBLIC = "public"
    UNKNOWN = "unknown"


class WifiSecurity(str, Enum):
    NOT_WIFI = "not_wifi"
    OPEN = "open"
    WEP = "wep"
    WPA = "wpa"
    WPA2 = "wpa2"
    WPA3 = "wpa3"
    UNKNOWN = "unknown"


class DnsProtection(str, Enum):
    ENCRYPTED_MANAGED = "encrypted_managed"
    ENCRYPTED_UNMANAGED = "encrypted_unmanaged"
    PLAINTEXT = "plaintext"
    UNKNOWN = "unknown"


class FirewallState(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class ServiceExposure(str, Enum):
    LOOPBACK = "loopback"
    LAN = "lan"
    PUBLIC = "public"


class NetworkTransport(str, Enum):
    HTTPS = "https"
    HTTP = "http"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ObservedService(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol: str = Field(pattern=r"^(tcp|udp)$")
    port: int = Field(ge=1, le=65535)
    exposure: ServiceExposure
    service_label: str | None = Field(default=None, max_length=100)

    @field_validator("service_label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = " ".join(value.split())
        return clean or None


class NetworkPostureObservation(BaseModel):
    """Bounded facts expected from a future signed native network report.

    This model intentionally contains no Wi-Fi password, router credential, packet payload,
    browsing history, arbitrary command output or public-IP geolocation data.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    network_scope: NetworkScope = NetworkScope.UNKNOWN
    wifi_security: WifiSecurity = WifiSecurity.UNKNOWN
    dns_protection: DnsProtection = DnsProtection.UNKNOWN
    firewall_state: FirewallState = FirewallState.UNKNOWN
    captive_portal_detected: bool = False
    gateway_admin_transport: NetworkTransport = NetworkTransport.UNKNOWN
    remote_router_administration_observed: bool = False
    services: tuple[ObservedService, ...] = Field(default_factory=tuple, max_length=128)


class NetworkPostureFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: FindingSeverity
    score: int = Field(ge=0, le=100)
    summary: str
    recommendation: str
    automatic_remediation_allowed: bool = False


class NetworkPostureAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    risk_score: int = Field(ge=0, le=100)
    evidence_completeness: str
    findings: tuple[NetworkPostureFinding, ...]
    automatic_router_login_attempted: bool = False
    port_scan_performed_by_control_plane: bool = False
    automatic_remediation_performed: bool = False
    truth: str


_SENSITIVE_PUBLIC_PORTS = {
    22: "SSH remote administration",
    23: "Telnet",
    135: "Windows RPC",
    139: "NetBIOS",
    445: "SMB file sharing",
    3389: "Remote Desktop",
    5900: "VNC remote control",
}


def _finding(
    code: str,
    severity: FindingSeverity,
    score: int,
    summary: str,
    recommendation: str,
) -> NetworkPostureFinding:
    return NetworkPostureFinding(
        code=code,
        severity=severity,
        score=score,
        summary=summary,
        recommendation=recommendation,
        automatic_remediation_allowed=False,
    )


def assess_network_posture(observation: NetworkPostureObservation) -> NetworkPostureAssessment:
    findings: list[NetworkPostureFinding] = []

    if observation.firewall_state == FirewallState.DISABLED:
        findings.append(
            _finding(
                "host_firewall_disabled",
                FindingSeverity.HIGH,
                38,
                "The device reported its host firewall disabled.",
                "Enable the operating-system firewall using its supported settings and verify required application exceptions before changing policy.",
            )
        )

    if observation.wifi_security == WifiSecurity.OPEN:
        findings.append(
            _finding(
                "open_wifi",
                FindingSeverity.HIGH,
                34,
                "The Wi-Fi network reported no link-layer encryption.",
                "Treat the network as untrusted, avoid sensitive local sharing and prefer a trusted encrypted network or an approved encrypted tunnel.",
            )
        )
    elif observation.wifi_security == WifiSecurity.WEP:
        findings.append(
            _finding(
                "obsolete_wep",
                FindingSeverity.HIGH,
                36,
                "The Wi-Fi network reported obsolete WEP security.",
                "Migrate the access point to WPA2 or preferably WPA3 with a strong unique network credential where the hardware supports it.",
            )
        )
    elif observation.wifi_security == WifiSecurity.WPA:
        findings.append(
            _finding(
                "legacy_wpa",
                FindingSeverity.MEDIUM,
                20,
                "The Wi-Fi network reported legacy WPA security.",
                "Upgrade the access point security to WPA2 or preferably WPA3 where supported.",
            )
        )

    if observation.network_scope == NetworkScope.PUBLIC:
        findings.append(
            _finding(
                "public_network",
                FindingSeverity.MEDIUM,
                18,
                "The active network is classified as public/untrusted.",
                "Use the operating system's public-network profile, disable unnecessary local sharing and use encrypted application protocols.",
            )
        )

    if observation.captive_portal_detected:
        findings.append(
            _finding(
                "captive_portal",
                FindingSeverity.LOW,
                8,
                "A captive portal was reported on the active network.",
                "Complete portal access cautiously and avoid entering unrelated credentials into unexpected network sign-in pages.",
            )
        )

    if observation.dns_protection == DnsProtection.PLAINTEXT:
        findings.append(
            _finding(
                "plaintext_dns",
                FindingSeverity.MEDIUM,
                16,
                "DNS resolution is reported as plaintext rather than encrypted.",
                "Consider a trusted encrypted DNS configuration supported by the operating system or managed network policy.",
            )
        )

    if observation.gateway_admin_transport == NetworkTransport.HTTP:
        findings.append(
            _finding(
                "router_admin_http",
                FindingSeverity.MEDIUM,
                20,
                "The local gateway administration interface was reported using unencrypted HTTP.",
                "Use HTTPS administration if the router supports it and avoid administering the router over untrusted networks.",
            )
        )

    if observation.remote_router_administration_observed:
        findings.append(
            _finding(
                "remote_router_admin_observed",
                FindingSeverity.HIGH,
                32,
                "Remote router administration appears enabled or externally reachable according to native evidence.",
                "Review the router's documented remote-management setting and disable external administration unless it is intentionally required and strongly protected.",
            )
        )

    for service in observation.services:
        if service.exposure == ServiceExposure.PUBLIC:
            known = _SENSITIVE_PUBLIC_PORTS.get(service.port)
            if known:
                findings.append(
                    _finding(
                        f"public_sensitive_service_{service.port}",
                        FindingSeverity.HIGH,
                        34,
                        f"{known} was reported as publicly exposed on {service.protocol.upper()} port {service.port}.",
                        "Confirm the exposure is intentional. Prefer firewall restriction, VPN/private access or service-specific hardening rather than broad public reachability.",
                    )
                )
            else:
                findings.append(
                    _finding(
                        f"public_service_{service.protocol}_{service.port}",
                        FindingSeverity.MEDIUM,
                        18,
                        f"A service was reported as publicly exposed on {service.protocol.upper()} port {service.port}.",
                        "Verify that the service must be internet-reachable and restrict it to the smallest required network scope.",
                    )
                )

    unknown_fields = sum(
        [
            observation.network_scope == NetworkScope.UNKNOWN,
            observation.wifi_security == WifiSecurity.UNKNOWN,
            observation.dns_protection == DnsProtection.UNKNOWN,
            observation.firewall_state == FirewallState.UNKNOWN,
            observation.gateway_admin_transport == NetworkTransport.UNKNOWN,
        ]
    )
    if unknown_fields >= 3:
        completeness = "limited"
    elif unknown_fields:
        completeness = "partial"
    else:
        completeness = "substantial"

    score = min(100, sum(item.score for item in findings))
    highest = max(
        (item.severity for item in findings),
        default=FindingSeverity.INFO,
        key=lambda value: {
            FindingSeverity.INFO: 0,
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }[value],
    )

    if completeness == "limited" and not findings:
        state = "insufficient_evidence"
    elif highest in {FindingSeverity.CRITICAL, FindingSeverity.HIGH} or score >= 60:
        state = "high_risk"
    elif findings:
        state = "attention_required"
    else:
        state = "healthy_observed_posture"

    return NetworkPostureAssessment(
        state=state,
        risk_score=score,
        evidence_completeness=completeness,
        findings=tuple(findings),
        automatic_router_login_attempted=False,
        port_scan_performed_by_control_plane=False,
        automatic_remediation_performed=False,
        truth=(
            "This assessment evaluates bounded network facts supplied by a trusted native evidence path. "
            "It does not log into routers, perform an internet port scan, inspect packet contents or automatically change network configuration."
        ),
    )


__all__ = [
    "DnsProtection",
    "FindingSeverity",
    "FirewallState",
    "NetworkPostureAssessment",
    "NetworkPostureFinding",
    "NetworkPostureObservation",
    "NetworkScope",
    "NetworkTransport",
    "ObservedService",
    "ServiceExposure",
    "WifiSecurity",
    "assess_network_posture",
]
