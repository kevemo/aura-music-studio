from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def validate() -> list[str]:
    errors: list[str] = []

    # Vercel is not a runtime/deployment target. The only retained file is a kill switch for a
    # legacy external GitHub integration that may still be installed on the repository.
    forbidden = (
        "vercel_bootstrap.py",
        "docs/VERCEL_DEPLOYMENT.md",
        "tests/test_vercel_runtime_config.py",
    )
    for path in forbidden:
        if (ROOT / path).exists():
            errors.append(f"obsolete Vercel runtime artifact must not exist: {path}")

    pyproject = tomllib.loads(_read("pyproject.toml"))
    if "vercel" in pyproject.get("tool", {}):
        errors.append("pyproject.toml must not define a Vercel runtime entrypoint")

    try:
        vercel = json.loads(_read("vercel.json"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"vercel.json kill switch is missing/invalid: {exc}")
    else:
        git = vercel.get("git") or {}
        if git.get("deploymentEnabled") is not False:
            errors.append("vercel.json must globally disable automatic Git deployments")
        forbidden_keys = {"buildCommand", "installCommand", "outputDirectory", "functions", "builds", "rewrites"}
        present = sorted(forbidden_keys.intersection(vercel))
        if present:
            errors.append("vercel.json must be disable-only; deployable keys present: " + ", ".join(present))

    prod_env = _read("deploy/production/production.env.example")
    if "example.com" in prod_env or "example.invalid" in prod_env:
        errors.append("production.env.example must not contain a fake production domain")
    for line in ("LSS_DEPLOYMENT_MODE=selfhost", "LSS_PUBLIC_BASE_URL=", "LSS_PUBLIC_SITE_ADDRESS="):
        if line not in prod_env:
            errors.append(f"production.env.example missing fail-closed self-host contract: {line}")
    for key in (
        "COSIGN_VERIFY_KEY",
        "AURA_SELFHOST_LLM_MODEL_DIR",
        "AURA_LLM_INTERNAL_API_KEY",
        "AURA_ACESTEP_CUDA_VISIBLE_DEVICES",
        "AURA_LLM_CUDA_VISIBLE_DEVICES",
    ):
        if not re.search(rf"(?m)^{re.escape(key)}=", prod_env):
            errors.append(f"production.env.example missing launcher-required setting: {key}")

    production_compose = _read("deploy/production/docker-compose.production.yml")
    if "LSS_DEPLOYMENT_MODE" not in production_compose:
        errors.append("production compose must require LSS_DEPLOYMENT_MODE")
    if "LSS_PUBLIC_SITE_ADDRESS: ${LSS_PUBLIC_SITE_ADDRESS:?" not in production_compose:
        errors.append("production Caddy service must require a real public site address")

    release_compose = _read("deploy/selfhost/compose.release.yml")
    for var in ("ESP_COMMAND_CENTER_IMAGE", "ESP_ACESTEP_IMAGE", "ESP_CADDY_IMAGE", "ESP_SEARXNG_IMAGE"):
        if f"${{{var}:?" not in release_compose:
            errors.append(f"release compose must require immutable runtime image variable {var}")

    runner = _read("deploy/selfhost/run-production.sh")
    required_runner_fragments = (
        "LSS_DEPLOYMENT_MODE must be selfhost",
        "--profile public",
        "--profile social-publishing",
        "LSS_PUBLIC_SITE_ADDRESS",
        "ESP_ACESTEP_IMAGE",
        "ESP_CADDY_IMAGE",
        "ESP_SEARXNG_IMAGE",
        "ace_step_upstream_commit",
        "component=ace-step",
        "Required production service is not running",
        'READY_URL="${LSS_PUBLIC_BASE_URL%/}/health/ready"',
    )
    for fragment in required_runner_fragments:
        if fragment not in runner:
            errors.append(f"production runner missing self-host release control: {fragment}")

    builder = _read("deploy/selfhost/build-release.sh")
    for fragment in (
        '"schema_version": 2',
        '"runtime_images"',
        "runtime_images_trivy_high_critical_gate",
        "ESP_ACESTEP_REGISTRY_IMAGE",
        "ESP_ACESTEP_UPSTREAM_COMMIT",
        "ace_step_upstream_commit",
        "ace_step_buildkit_provenance",
        "ace_step_cosign_signature_verified",
        "component=ace-step",
        "ESP_CADDY_IMAGE",
        "ESP_SEARXNG_IMAGE",
    ):
        if fragment not in builder:
            errors.append(f"release builder missing immutable runtime evidence: {fragment}")

    manifest = json.loads(_read("deploy/selfhost/release-manifest.example.json"))
    if manifest.get("schema_version") != 2:
        errors.append("release manifest template must use schema 2")
    if manifest.get("approved") is not False:
        errors.append("release manifest template must fail closed with approved=false")
    if manifest.get("git_sha") or manifest.get("command_center_image"):
        errors.append("release manifest template must not contain fake release identity/image values")
    runtime = manifest.get("runtime_images") or {}
    if any(runtime.get(key) for key in ("ace_step", "caddy", "searxng")):
        errors.append("release manifest template must not contain fake runtime image digests")
    if manifest.get("ace_step_upstream_commit"):
        errors.append("release manifest template must not contain a fake ACE-Step source identity")
    evidence = manifest.get("supply_chain") or {}
    for key in ("ace_step_buildkit_provenance", "ace_step_buildkit_sbom", "ace_step_cosign_signature_verified"):
        if evidence.get(key) is not False:
            errors.append(f"release manifest template must fail closed for {key}")

    # Guard against accidentally reintroducing a serverless FastAPI entrypoint.
    for path in ("pyproject.toml", "vercel.json"):
        text = _read(path)
        if re.search(r"vercel_bootstrap|vercel\s*:\s*app", text, re.IGNORECASE):
            errors.append(f"serverless application entrypoint reintroduced in {path}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Self-host release contract FAILED:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print("Self-host release contract OK: self-host is authoritative and production remains fail-closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
