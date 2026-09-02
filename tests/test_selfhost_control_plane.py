from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELFHOST = ROOT / "deploy" / "selfhost"


def test_selfhost_is_authoritative_and_vercel_is_disabled_legacy_integration():
    contract = json.loads((SELFHOST / "control-plane.json").read_text(encoding="utf-8"))
    assert contract["product"] == "Elevate Souls Productions Content Creation Command Center"
    assert contract["endorsement"] == "Powered by Aura AI"
    assert contract["authoritative_runtime"] == "esp-self-host"
    assert contract["vercel_role"] == "disabled-legacy-integration"
    assert contract["target_platform"]["runtime_dependency_on_vercel"] is False

    vercel = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert vercel["git"]["deploymentEnabled"] is False


def test_current_sqlite_mode_refuses_fake_horizontal_scaling_claim():
    contract = json.loads((SELFHOST / "control-plane.json").read_text(encoding="utf-8"))
    safe = contract["current_safe_mode"]
    assert safe["database"] == "sqlite-local-filesystem"
    assert safe["command_center_writer_hosts"] == 1
    assert safe["horizontal_web_scaling"] is False
    gates = {gate["id"]: gate for gate in contract["scale_gates"]}
    assert gates["postgresql-ha-migration"]["status"] == "blocked"
    assert gates["object-storage-asset-abstraction"]["status"] == "blocked"
    assert gates["distributed-durable-job-queue"]["status"] == "blocked"


def test_target_platform_has_owned_registry_scaling_secrets_and_supply_chain():
    contract = json.loads((SELFHOST / "control-plane.json").read_text(encoding="utf-8"))
    target = contract["target_platform"]
    assert target["oci_registry_target"] == "harbor-ha"
    assert target["registry_vulnerability_scanning"] == "trivy"
    assert target["autoscaling"] == "keda-plus-kubernetes-hpa"
    assert target["image_signing"] == "cosign"
    assert target["secret_management"] == "openbao-ha-or-reviewed-compatible"
    assert "hubble" in target["telemetry"]
    gates = {gate["id"]: gate for gate in contract["scale_gates"]}
    assert gates["registry-ha-and-vulnerability-policy"]["status"] == "blocked"
    assert gates["signed-image-admission"]["status"] == "blocked"
    assert gates["dynamic-secrets-ha"]["status"] == "blocked"


def test_release_manifest_template_is_schema2_non_runnable_and_untrusted():
    example = json.loads((SELFHOST / "release-manifest.example.json").read_text(encoding="utf-8"))
    assert example["schema_version"] == 2
    assert example["git_sha"] == ""
    assert example["command_center_image"] == ""
    assert example["runtime_images"] == {
        "ace_step": "",
        "caddy": "",
        "searxng": "",
    }
    assert example["approved"] is False
    evidence = example["supply_chain"]
    assert evidence
    assert all(value is False for value in evidence.values())


def test_release_builder_requires_scan_signature_sbom_and_provenance():
    script = (SELFHOST / "build-release.sh").read_text(encoding="utf-8")
    for required in (
        "docker buildx",
        "--provenance=mode=max",
        "--sbom=true",
        "trivy image",
        "--severity HIGH,CRITICAL",
        "COSIGN_SIGNING_KEY",
        "COSIGN_VERIFY_KEY",
        "cosign sign",
        "cosign verify",
        '"approved": False',
        '"trivy_unfixed_high_critical_gate": True',
        '"runtime_images_trivy_high_critical_gate": True',
    ):
        assert required in script
    assert "Mutable latest tags are forbidden" in script
    assert "--ignore-unfixed" not in script


def test_production_wrapper_is_fail_closed_and_reverifies_supply_chain_and_readiness():
    script = (SELFHOST / "run-production.sh").read_text(encoding="utf-8")
    for required in (
        "AURA_REQUIRE_LIVE_RENDERER",
        "ACESTEP_API_KEY",
        "AURA_MONITORING_TOKEN",
        "LSS_BACKUP_AGE_RECIPIENT",
        "COSIGN_VERIFY_KEY",
        "Release manifest supply-chain evidence is incomplete",
        '"buildkit_provenance"',
        '"buildkit_sbom"',
        '"trivy_high_critical_gate"',
        '"trivy_unfixed_high_critical_gate"',
        '"runtime_images_trivy_high_critical_gate"',
        '"cosign_signature_verified"',
        "cosign verify",
        "trivy image",
        "--severity HIGH,CRITICAL",
        "/health/ready",
        "ace-step",
        "caddy",
        "searxng",
        "--profile",
        "public",
        "--no-build",
        "@sha256:",
    ):
        assert required in script
    assert "--ignore-unfixed" not in script
    assert "ESP authoritative self-host release is READY" in script


def test_kubernetes_bootstrap_forbids_mutable_release_channels_and_unsafe_cluster_mutation():
    script = (SELFHOST / "bootstrap-k8s.sh").read_text(encoding="utf-8")
    assert '"latest"' in script
    assert '"stable"' in script
    assert '"main"' in script
    assert "ESP_BOOTSTRAP_NEW_DEDICATED_CLUSTER" in script
    assert "ESP_CONFIRM_CILIUM_PRIMARY_CNI" in script
    assert "ESP_CONFIRM_BARE_METAL_LOAD_BALANCER" in script
    assert "gpu-operator" in script
    assert "cloudnative-pg" in script
    assert "kyverno" in script
    assert "kedacore/keda" in script
    assert "opentelemetry-collector" in script
    assert "rook-ceph" in script
    assert "hubble.relay.enabled=true" in script
    assert "--timeout=300s\n" in script
    assert "--timeout=300s || true" not in script


def test_network_policy_starts_default_deny_and_renderer_is_private():
    policy = (SELFHOST / "kubernetes" / "network-policy-baseline.yaml").read_text(encoding="utf-8")
    assert "name: default-deny-ingress" in policy
    assert "name: default-deny-egress" in policy
    assert "name: ace-step-private-ingress" in policy
    assert "port: 8001" in policy
