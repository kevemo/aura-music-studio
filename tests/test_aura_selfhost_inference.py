from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SELFHOST = ROOT / "deploy" / "selfhost"


def test_aura_runtime_is_private_openai_compatible_and_not_external_api_dependent():
    text = (SELFHOST / "compose.aura-inference.yml").read_text(encoding="utf-8")
    assert "AURA_INTELLIGENCE_PROVIDER: openai_compatible" in text
    assert "AURA_LLM_BASE_URL: http://aura-llm:8000" in text
    assert "internal: true" in text
    assert "ports:" not in text
    assert "AURA_VLLM_IMAGE:?" in text
    assert ":/models/aura:ro" in text
    assert 'user: "2000:0"' in text
    assert "VLLM_NO_USAGE_STATS" in text
    assert "HF_HUB_DISABLE_TELEMETRY" in text
    assert "--no-enable-log-requests" in text
    assert "--disable-uvicorn-access-log" in text

    contract = json.loads((SELFHOST / "control-plane.json").read_text(encoding="utf-8"))
    assert contract["target_platform"]["runtime_dependency_on_external_llm_api"] is False
    assert contract["current_safe_mode"]["public_aura_inference_ports"] is False
    gates = {row["id"]: row for row in contract["scale_gates"]}
    assert gates["aura-selfhost-llm-live-e2e"]["status"] == "blocked"


def test_inference_manifest_template_is_immutable_and_untrusted_by_default():
    data = json.loads((SELFHOST / "aura-inference-manifest.example.json").read_text(encoding="utf-8"))
    assert "@sha256:" in data["vllm_image"]
    assert len(data["model_manifest_sha256"]) == 64
    assert data["approved"] is False
    assert all(value is False for value in data["supply_chain"].values())


def test_model_integrity_seal_and_verify_detects_changes(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"test"}\n', encoding="utf-8")
    (model / "weights.safetensors").write_bytes(b"real-test-weight-bytes")
    tool = SELFHOST / "aura_model_integrity.py"

    sealed = subprocess.run(
        [sys.executable, str(tool), "seal", str(model)], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert len(sealed) == 64
    manifest = model / "MODEL_SHA256SUMS.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == sealed

    subprocess.run([sys.executable, str(tool), "verify", str(model), sealed], check=True)
    (model / "weights.safetensors").write_bytes(b"tampered")
    failed = subprocess.run([sys.executable, str(tool), "verify", str(model), sealed], capture_output=True, text=True)
    assert failed.returncode != 0
    assert "digest mismatch" in (failed.stderr + failed.stdout)


def test_prepare_inference_and_deploy_are_fail_closed():
    prepare = (SELFHOST / "prepare-aura-inference.sh").read_text(encoding="utf-8")
    for token in (
        "@sha256:", "trivy image", "--severity HIGH,CRITICAL", "cosign verify",
        "component=aura-selfhost-inference", "approved\": False", "aura_model_integrity.py",
    ):
        assert token in prepare
    assert "--ignore-unfixed" not in prepare

    deploy = (SELFHOST / "run-production.sh").read_text(encoding="utf-8")
    for token in (
        "--inference", "Aura inference manifest is not approved", "model_file_hashes_verified",
        "AURA_ACESTEP_CUDA_VISIBLE_DEVICES", "AURA_LLM_CUDA_VISIBLE_DEVICES",
        "GPU assignments overlap", "aura_model_integrity.py", "component=aura-selfhost-inference",
        "compose.aura-inference.yml", "aura-llm", "/v1/models", "Approved Aura model",
    ):
        assert token in deploy
    assert "--ignore-unfixed" not in deploy


def test_gpu_music_and_reasoning_defaults_are_separate():
    music = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    inference = (SELFHOST / "compose.aura-inference.yml").read_text(encoding="utf-8")
    assert "AURA_ACESTEP_CUDA_VISIBLE_DEVICES:-0" in music
    assert "AURA_LLM_CUDA_VISIBLE_DEVICES:-1" in inference


def test_kubernetes_policy_keeps_aura_llm_private():
    policy = (SELFHOST / "kubernetes" / "network-policy-baseline.yaml").read_text(encoding="utf-8")
    assert "name: aura-llm-private-ingress" in policy
    assert "app.kubernetes.io/name: aura-llm" in policy
    assert "port: 8000" in policy
