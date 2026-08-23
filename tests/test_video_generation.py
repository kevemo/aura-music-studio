import os
from pathlib import Path

import pytest

from aura_music_studio.video_generation import (
    VideoGenerationError,
    VideoGenerationRequest,
    VideoGenerationService,
)


def test_rejects_missing_provider(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("AURA_VIDEO_RENDER_CMD", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RUNWAYML_API_SECRET", raising=False)
    service = VideoGenerationService(tmp_path)
    with pytest.raises(VideoGenerationError, match="No real video provider"):
        service.generate(VideoGenerationRequest(prompt="A singer on a moonlit stage"))


def test_image_mode_requires_reference(tmp_path: Path):
    service = VideoGenerationService(tmp_path)
    with pytest.raises(VideoGenerationError, match="requires a reference"):
        service.generate(VideoGenerationRequest(prompt="Animate this artwork", mode="image_to_video", provider="local"))


def test_local_provider_writes_real_output(monkeypatch, tmp_path: Path):
    renderer = tmp_path / "renderer.py"
    renderer.write_text(
        "import os\nfrom pathlib import Path\nout=Path(os.environ['AURA_VIDEO_OUTPUT']); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b'0' * 2048)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_VIDEO_RENDER_CMD", f"{os.sys.executable} {renderer}")
    service = VideoGenerationService(tmp_path / "out")
    result = service.generate(VideoGenerationRequest(prompt="A cinematic performance", provider="local"))
    assert result.status == "completed"
    assert result.provider == "local"
    assert result.output_path
    assert Path(result.output_path).exists()
    assert Path(result.output_path).stat().st_size >= 1024
    assert len(service.provenance_hash(result)) == 64


def test_invalid_ratio_is_rejected(tmp_path: Path):
    service = VideoGenerationService(tmp_path)
    with pytest.raises(VideoGenerationError, match="Unsupported aspect ratio"):
        service.generate(VideoGenerationRequest(prompt="test", aspect_ratio="4:3", provider="local"))
