from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .acestep_api import AceStepClient, AceStepRequest
from .renderer_runtime import probe_real_audio


def run_ace_step_smoke(*, status_path: str | Path | None = None) -> dict:
    """Prove the configured ACE-Step service can perform a real neural inference.

    HTTP health alone is not sufficient: this submits a short instrumental task, downloads the
    returned waveform, decodes it, records safe readiness metadata, then removes the test audio.
    """
    model = os.getenv("AURA_ACESTEP_SMOKE_MODEL") or os.getenv("AURA_ACESTEP_FULL_MODEL") or "acestep-v15-turbo"
    work = Path(os.getenv("AURA_RENDERER_SMOKE_DIR", "/tmp/aura-renderer-smoke")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    client = AceStepClient(timeout=int(os.getenv("AURA_RENDERER_SMOKE_TIMEOUT", "1200")))
    result: dict = {
        "engine": "ACE-Step 1.5",
        "model": model,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "real_audio_verified": False,
    }
    try:
        outputs = client.generate(
            AceStepRequest(
                prompt=(
                    "Original short instrumental studio test, warm electric piano, live bass, soft drums, "
                    "clean production, no vocals, no copyrighted melody"
                ),
                lyrics="[Instrumental]",
                task_type="text2music",
                model=model,
                audio_duration=float(os.getenv("AURA_RENDERER_SMOKE_SECONDS", "10")),
                thinking=False,
                use_format=False,
                audio_format="wav",
                inference_steps=int(os.getenv("AURA_RENDERER_SMOKE_STEPS", "4")),
                batch_size=1,
            ),
            work,
        )
        probe = probe_real_audio(outputs[0], minimum_seconds=3.0)
        if not probe.valid:
            raise RuntimeError(f"ACE-Step smoke render was not valid real audio: {probe.error}")
        result.update({
            "real_audio_verified": True,
            "duration_seconds": probe.duration_seconds,
            "sample_rate": probe.sample_rate,
            "channels": probe.channels,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return result
    except Exception as exc:
        result.update({
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        raise
    finally:
        if status_path is not None:
            target = Path(status_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, indent=2), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    status = os.getenv("AURA_RENDERER_SMOKE_STATUS", "/app/data/renderer_smoke.json")
    result = run_ace_step_smoke(status_path=status)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
