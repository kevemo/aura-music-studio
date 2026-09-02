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


def test_production_environment_template_has_no_fake_public_origin():
    text = (ROOT / "deploy/production/production.env.example").read_text(encoding="utf-8")
    assert "LSS_DEPLOYMENT_MODE=selfhost" in text
    assert "LSS_PUBLIC_BASE_URL=\n" in text
    assert "LSS_PUBLIC_SITE_ADDRESS=\n" in text
    assert "example.com" not in text
    assert "example.invalid" not in text
