from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vercel_entrypoint_targets_real_fastapi_bootstrap():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["vercel"]["entrypoint"] == "vercel_bootstrap:app"


def test_vercel_function_excludes_non_runtime_payload():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    fn = config["functions"]["app.py"]
    assert fn["maxDuration"] == 60
    excludes = fn["excludeFiles"]
    assert "tests/**" in excludes
    assert "preview/**" in excludes
    assert "deploy/**" in excludes


def test_vercel_bootstrap_uses_tmp_and_preserves_explicit_operator_values():
    code = r'''
import json, os
os.environ["VERCEL"] = "1"
os.environ["VERCEL_URL"] = "preview.example.vercel.app"
os.environ["AURA_PROJECTS_ROOT"] = "/tmp/operator-projects"
import vercel_bootstrap
print(json.dumps({
    "projects": os.environ["AURA_PROJECTS_ROOT"],
    "db": os.environ["LSS_DB_PATH"],
    "social": os.environ["AURA_SOCIAL_ROOT"],
    "base": os.environ["LSS_PUBLIC_BASE_URL"],
    "secure": os.environ["LSS_COOKIE_SECURE"],
}))
'''
    env = dict(os.environ)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["projects"] == "/tmp/operator-projects"
    assert payload["db"].startswith("/tmp/pulsar-frequency-house/")
    assert payload["social"].startswith("/tmp/pulsar-frequency-house/")
    assert payload["base"] == "https://preview.example.vercel.app"
    assert payload["secure"] == "true"
