from aura_music_studio.video_generation import VideoGenerationRequest, VideoGenerationService


def test_video_request_defaults_are_social_ready(tmp_path):
    service = VideoGenerationService(tmp_path)
    request = VideoGenerationRequest(prompt="cinematic live performance")
    assert request.aspect_ratio == "9:16"
    assert request.duration_seconds == 8
    assert request.provider == "auto"
    assert "text_to_video" in service.VALID_MODES
