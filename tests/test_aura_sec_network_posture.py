import pytest
from pydantic import ValidationError

from aura_music_studio.aura_sec_network_posture import (
    DnsProtection,
    FirewallState,
    NetworkPostureObservation,
    NetworkScope,
    NetworkTransport,
    ObservedService,
    ServiceExposure,
    WifiSecurity,
    assess_network_posture,
)


def test_strong_observed_posture_can_be_reported_healthy_without_active_network_control():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.TRUSTED_PRIVATE,
            wifi_security=WifiSecurity.WPA3,
            dns_protection=DnsProtection.ENCRYPTED_MANAGED,
            firewall_state=FirewallState.ENABLED,
            captive_portal_detected=False,
            gateway_admin_transport=NetworkTransport.HTTPS,
            remote_router_administration_observed=False,
            services=(
                ObservedService(protocol="tcp", port=631, exposure=ServiceExposure.LAN, service_label="printer"),
            ),
        )
    )
    assert result.state == "healthy_observed_posture"
    assert result.risk_score == 0
    assert result.evidence_completeness == "substantial"
    assert result.findings == ()
    assert result.automatic_router_login_attempted is False
    assert result.port_scan_performed_by_control_plane is False
    assert result.automatic_remediation_performed is False


def test_unknown_evidence_is_never_mislabeled_healthy():
    result = assess_network_posture(NetworkPostureObservation())
    assert result.state == "insufficient_evidence"
    assert result.evidence_completeness == "limited"
    assert result.risk_score == 0


def test_open_wifi_plaintext_dns_and_public_network_require_attention():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PUBLIC,
            wifi_security=WifiSecurity.OPEN,
            dns_protection=DnsProtection.PLAINTEXT,
            firewall_state=FirewallState.ENABLED,
            captive_portal_detected=True,
            gateway_admin_transport=NetworkTransport.UNAVAILABLE,
        )
    )
    codes = {item.code for item in result.findings}
    assert result.state == "high_risk"
    assert "open_wifi" in codes
    assert "plaintext_dns" in codes
    assert "public_network" in codes
    assert "captive_portal" in codes
    assert all(item.automatic_remediation_allowed is False for item in result.findings)


def test_wep_and_legacy_wpa_are_distinguished():
    wep = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.WEP,
            dns_protection=DnsProtection.ENCRYPTED_UNMANAGED,
            firewall_state=FirewallState.ENABLED,
            gateway_admin_transport=NetworkTransport.HTTPS,
        )
    )
    wpa = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.WPA,
            dns_protection=DnsProtection.ENCRYPTED_UNMANAGED,
            firewall_state=FirewallState.ENABLED,
            gateway_admin_transport=NetworkTransport.HTTPS,
        )
    )
    assert any(item.code == "obsolete_wep" and item.severity.value == "high" for item in wep.findings)
    assert any(item.code == "legacy_wpa" and item.severity.value == "medium" for item in wpa.findings)


def test_disabled_host_firewall_is_high_risk_evidence():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.NOT_WIFI,
            dns_protection=DnsProtection.ENCRYPTED_MANAGED,
            firewall_state=FirewallState.DISABLED,
            gateway_admin_transport=NetworkTransport.UNAVAILABLE,
        )
    )
    finding = next(item for item in result.findings if item.code == "host_firewall_disabled")
    assert finding.severity.value == "high"
    assert result.state == "high_risk"


def test_public_rdp_and_smb_exposure_are_high_risk_but_lan_services_are_not_automatically_flagged():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.NOT_WIFI,
            dns_protection=DnsProtection.ENCRYPTED_MANAGED,
            firewall_state=FirewallState.ENABLED,
            gateway_admin_transport=NetworkTransport.HTTPS,
            services=(
                ObservedService(protocol="tcp", port=3389, exposure=ServiceExposure.PUBLIC),
                ObservedService(protocol="tcp", port=445, exposure=ServiceExposure.PUBLIC),
                ObservedService(protocol="tcp", port=8123, exposure=ServiceExposure.LAN),
            ),
        )
    )
    codes = {item.code for item in result.findings}
    assert "public_sensitive_service_3389" in codes
    assert "public_sensitive_service_445" in codes
    assert "public_service_tcp_8123" not in codes
    assert result.state == "high_risk"


def test_unknown_public_service_is_warning_not_proof_of_compromise():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.NOT_WIFI,
            dns_protection=DnsProtection.ENCRYPTED_MANAGED,
            firewall_state=FirewallState.ENABLED,
            gateway_admin_transport=NetworkTransport.HTTPS,
            services=(ObservedService(protocol="tcp", port=8443, exposure=ServiceExposure.PUBLIC),),
        )
    )
    finding = next(item for item in result.findings if item.code == "public_service_tcp_8443")
    assert finding.severity.value == "medium"
    assert "Verify" in finding.recommendation
    assert result.state == "attention_required"


def test_remote_router_admin_and_plain_http_admin_are_findings_but_never_auto_changed():
    result = assess_network_posture(
        NetworkPostureObservation(
            network_scope=NetworkScope.PRIVATE,
            wifi_security=WifiSecurity.WPA2,
            dns_protection=DnsProtection.ENCRYPTED_MANAGED,
            firewall_state=FirewallState.ENABLED,
            gateway_admin_transport=NetworkTransport.HTTP,
            remote_router_administration_observed=True,
        )
    )
    codes = {item.code for item in result.findings}
    assert "router_admin_http" in codes
    assert "remote_router_admin_observed" in codes
    assert result.automatic_router_login_attempted is False
    assert result.automatic_remediation_performed is False
    assert "does not log into routers" in result.truth


def test_observation_schema_rejects_arbitrary_fields_credentials_and_invalid_ports():
    with pytest.raises(ValidationError):
        NetworkPostureObservation.model_validate(
            {
                "network_scope": "private",
                "wifi_security": "wpa3",
                "dns_protection": "encrypted_managed",
                "firewall_state": "enabled",
                "wifi_password": "must-not-be-accepted",
            }
        )

    with pytest.raises(ValidationError):
        ObservedService(protocol="tcp", port=70000, exposure=ServiceExposure.PUBLIC)

    with pytest.raises(ValidationError):
        ObservedService.model_validate(
            {"protocol": "icmp", "port": 7, "exposure": "public"}
        )


def test_service_count_is_bounded_to_limit_native_report_size():
    services = tuple(
        ObservedService(protocol="tcp", port=1000 + index, exposure=ServiceExposure.LAN)
        for index in range(129)
    )
    with pytest.raises(ValidationError):
        NetworkPostureObservation(services=services)
