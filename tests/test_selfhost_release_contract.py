from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_selfhost_release_contract import ROOT, validate


def test_repository_selfhost_release_contract_is_complete():
    assert validate() == []


def test_legacy_git_integration_is_disable_only():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["git"]["deploymentEnabled"] is False
    assert "functions" not in config
    assert "builds" not in config
    assert "buildCommand" not in config
    assert not (ROOT / "vercel_bootstrap.py").exists()


def test_release_template_cannot_be_deployed_as_if_real():
    manifest = json.loads((ROOT / "deploy/selfhost/release-manifest.example.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["approved"] is False
    assert manifest["git_sha"] == ""
    assert manifest["command_center_image"] == ""
    assert set(manifest["runtime_images"]) == {"ace_step", "caddy", "searxng"}
    assert all(value == "" for value in manifest["runtime_images"].values())
    assert manifest["ace_step_upstream_commit"] == ""
    assert manifest["supply_chain"]["ace_step_buildkit_provenance"] is False
    assert manifest["supply_chain"]["ace_step_buildkit_sbom"] is False
    assert manifest["supply_chain"]["ace_step_cosign_signature_verified"] is False


def test_production_environment_template_has_no_fake_public_origin_and_is_launcher_complete():
    text = (ROOT / "deploy/production/production.env.example").read_text(encoding="utf-8")
    assert "LSS_DEPLOYMENT_MODE=selfhost" in text
    assert "LSS_PUBLIC_BASE_URL=\n" in text
    assert "LSS_PUBLIC_SITE_ADDRESS=\n" in text
    assert "example.com" not in text
    assert "example.invalid" not in text
    for key in (
        "COSIGN_VERIFY_KEY",
        "AURA_SELFHOST_LLM_MODEL_DIR",
        "AURA_LLM_INTERNAL_API_KEY",
        "AURA_ACESTEP_CUDA_VISIBLE_DEVICES",
        "AURA_LLM_CUDA_VISIBLE_DEVICES",
    ):
        assert f"{key}=" in text


def test_release_builder_owns_ace_step_source_identity_and_signature():
    builder = (ROOT / "deploy/selfhost/build-release.sh").read_text(encoding="utf-8")
    assert "ESP_ACESTEP_REGISTRY_IMAGE" in builder
    assert "ESP_ACESTEP_UPSTREAM_COMMIT" in builder
    assert "ACE-Step-1.5.git#${ACESTEP_UPSTREAM_COMMIT}" in builder
    assert 'component=ace-step' in builder
    assert 'upstream_commit=$ACESTEP_UPSTREAM_COMMIT' in builder


def test_production_runner_starts_social_profile_only_when_explicitly_enabled():
    runner = (ROOT / "deploy/selfhost/run-production.sh").read_text(encoding="utf-8")
    assert "--profile public" in runner
    assert "--profile social-publishing" in runner
    assert 'AURA_SOCIAL_PUBLISH_WORKER_ENABLED:-false' in runner
    assert 'REQUIRED_SERVICES+=(esp-social-publish-worker)' in runner
    assert 'component=ace-step' in runner
    assert 'upstream_commit=$EXPECTED_ACESTEP_COMMIT' in runner
