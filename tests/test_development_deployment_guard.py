from __future__ import annotations

import json
from pathlib import Path


def test_active_development_branches_are_vercel_disabled():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    deployment_enabled = config.get("git", {}).get("deploymentEnabled", {})

    protected_branches = {
        "development/full-site-build",
        "feature/aura-platform-core",
        "feature/esp-backstage-evidence",
        "feature/creative-studios-game-forge",
        "feature/credit-wallet-foundation",
        "feature/hourly-auto-build",
        "integration/control-room",
    }

    missing_or_enabled = {
        branch
        for branch in protected_branches
        if deployment_enabled.get(branch) is not False
    }
    assert not missing_or_enabled, (
        "Active Pulsar development branches must be explicitly disabled for Vercel Git deployments: "
        f"{sorted(missing_or_enabled)}"
    )
