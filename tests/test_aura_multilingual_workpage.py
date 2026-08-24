import os
import zipfile
from pathlib import Path

import pytest

from aura_music_studio.aura_attachment_understanding import AuraAttachmentUnderstandingService
from aura_music_studio.aura_persona import AURA_PERSONA_NAME, persona_context
from aura_music_studio.music_video_orchestrator import MusicVideoStore
from aura_music_studio.song_languages import resolve_song_language
from aura_music_studio.video_generation import (
    VideoGenerationError,
    VideoGenerationRequest,
    VideoGenerationService,
)


def test_spanish_song_language_maps_to_native_ace_code():
    language = resolve_song_language("Spanish")
    assert language.locale == "es"
    assert language.ace_vocal_language == "es"
    assert language.ace_direct_support is True


def test_script_locale_preserves_ui_locale_but_routes_base_vocal_language():
    language = resolve_song_language("Traditional Chinese")
    assert language.locale == "zh-Hant"
    assert language.language_code == "zh"
    assert language.ace_vocal_language == "zh"


def test_aura_persona_context_is_locale_and_workspace_aware():
    context = persona_context("es", "studio")
    assert context["name"] == AURA_PERSONA_NAME
    assert context["response_locale"] == "es"
    assert context["workspace_mode"] == "studio"
    assert "sovereignty" in context["system_persona"].lower()
    assert "guru" in context["system_persona"].lower()


def test_local_video_router_can_select_ltx2(monkeypatch, tmp_path: Path):
    renderer = tmp_path / "renderer.py"
    record = tmp_path / "engine.txt"
    renderer.write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(record)!r}).write_text(os.environ['AURA_VIDEO_ENGINE'] + '|' + os.environ['AURA_VIDEO_MODEL'])\n"
        "out=Path(os.environ['AURA_VIDEO_OUTPUT']); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b'v' * 4096)\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AURA_VIDEO_RENDER_CMD", raising=False)
    monkeypatch.setenv("AURA_LTX2_CMD", f"{os.sys.executable} {renderer}")
    monkeypatch.setenv("AURA_LTX2_MODEL", "LTX-test")
    service = VideoGenerationService(tmp_path / "out")
    result = service.generate(VideoGenerationRequest(prompt="cinematic stage", provider="local"))
    assert result.status == "completed"
    assert result.model == "LTX-test"
    assert record.read_text() == "ltx2|LTX-test"


def test_openai_square_request_fails_instead_of_silent_landscape(tmp_path: Path):
    service = VideoGenerationService(tmp_path)
    with pytest.raises(VideoGenerationError, match="not native square"):
        service._openai_size("1:1", "standard")


def test_runway_text_to_video_square_fails_closed():
    request = VideoGenerationRequest(prompt="square performance", mode="text_to_video", aspect_ratio="1:1")
    with pytest.raises(VideoGenerationError, match="native square"):
        VideoGenerationService._runway_ratio(request)


def test_docx_attachment_is_actually_parsed(tmp_path: Path):
    docx = tmp_path / "brief.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Aura production brief</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Spanish chorus requested</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", xml)
    result = AuraAttachmentUnderstandingService().understand(
        docx,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result["parsed"] is True
    assert result["format"] == "docx"
    assert "Aura production brief" in result["text_excerpt"]
    assert "Spanish chorus requested" in result["text_excerpt"]


def test_music_video_store_keeps_provider_render_identity_separate(tmp_path: Path):
    store = MusicVideoStore(tmp_path / "studio.sqlite3")
    store.save_project(
        {
            "id": "mv1",
            "user_id": "user1",
            "source_project": "song-one",
            "title": "Song One",
            "concept": "cinematic",
            "aspect_ratio": "16:9",
            "provider": "auto",
            "quality": "standard",
            "audio_path": "/tmp/master.wav",
            "storyboard": [],
            "status": "submitting",
        }
    )
    shot_id = store.save_shot(
        "mv1",
        "user1",
        {
            "index": 1,
            "section": "Verse 1",
            "start_seconds": 0.0,
            "end_seconds": 8.0,
            "requested_seconds": 8,
            "prompt": "verse shot",
        },
        {
            "id": "provider-result-123",
            "provider": "openai",
            "model": "sora-2",
            "provider_job_id": "remote-job-999",
            "status": "submitted",
            "output_path": None,
            "error": None,
        },
    )
    project = store.get_project("user1", "mv1")
    assert project["shots"][0]["id"] == shot_id
    assert project["shots"][0]["render_result_id"] == "provider-result-123"
    assert project["shots"][0]["provider_job_id"] == "remote-job-999"
