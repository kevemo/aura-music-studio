from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_BRAND = "Elevate Souls Productions Content Creation Command Center"
ENDORSEMENT = "Powered by Aura AI"
RETIRED_PUBLIC_BRAND = "Pulsar-Frequency House"


def test_package_metadata_uses_authoritative_public_brand():
    pyproject_path = ROOT / "pyproject.toml"
    text = pyproject_path.read_text(encoding="utf-8")
    config = tomllib.loads(text)
    project = config["project"]

    assert project["name"] == "aura-music-studio"  # compatibility identifier
    assert AUTHORITATIVE_BRAND in project["description"]
    assert ENDORSEMENT in project["description"]
    assert RETIRED_PUBLIC_BRAND not in text
