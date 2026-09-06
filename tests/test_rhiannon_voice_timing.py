from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import pytest

from aura_music_studio.aura_voice_conversation import AuraVoiceConversationMiddleware, VOICE_SCRIPT
from aura_music_studio.rhiannon_voice_contracts import SpeechTimingTrack, SpeechTimingSpan, TimingKind
from aura_music_studio.rhiannon_voice_timing import (
    RHIANNON_TIMING_SCRIPT,
    canonical_viseme_schedule,
    router as timing_router,
    timing_consumption_contract,
)


def test_precise_schedule_accepts_canonical_non_overlapping_visemes():
    track = SpeechTimingTrack(
        audio_duration_ms=900,
        precise_timing=True,
        source="runtime",
        spans=[
            SpeechTimingSpan(kind=TimingKind.VISEME, value="sil", start_ms=0, end_ms=90),
            SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=90, end_ms=410, confidence=0.9),
            SpeechTimingSpan(kind=TimingKind.VISEME, value="e", start_ms=410, end_ms=700),
        ],
    )
    schedule = canonical_viseme_schedule(track)
    assert [row["viseme"] for row in schedule] == ["sil", "aa", "e"]
    assert schedule[1]["confidence"] == 0.9


def test_precise_schedule_rejects_fallback_promotion_and_overlap():
    with pytest.raises(ValueError, match="fallback timing cannot be promoted"):
        canonical_viseme_schedule(
            SpeechTimingTrack(
                audio_duration_ms=400,
                precise_timing=True,
                source="fallback",
                spans=[SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=0, end_ms=400)],
            )
        )

    with pytest.raises(ValueError, match="must not overlap"):
        canonical_viseme_schedule(
            SpeechTimingTrack(
                audio_duration_ms=500,
                precise_timing=True,
                source="runtime",
                spans=[
                    SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=0, end_ms=300),
                    SpeechTimingSpan(kind=TimingKind.VISEME, value="e", start_ms=250, end_ms=500),
                ],
            )
        )


def test_precise_schedule_rejects_browser_budget_exhaustion():
    spans = [
        SpeechTimingSpan(kind=TimingKind.VISEME, value="aa", start_ms=i, end_ms=i + 1)
        for i in range(4001)
    ]
    track = SpeechTimingTrack(audio_duration_ms=5000, precise_timing=True, source="runtime", spans=spans)
    with pytest.raises(ValueError, match="browser consumption budget"):
        canonical_viseme_schedule(track)


def test_browser_timing_consumer_uses_real_media_clock_and_stale_job_gate():
    script = RHIANNON_TIMING_SCRIPT
    assert "RhiannonVoice.timing/v1" in script
    assert "audio?.currentTime" in script
    assert "requestAnimationFrame" in script
    assert "RhiannonTurnHost" in script
    assert "acceptsSpeechJob" in script
    assert "lip_sync_mode:'canonical_timing'" in script
    assert "precise_timing:true" in script
    assert "source:'canonical_timing'" in script
    assert "duration_mismatch" in script


def test_amplitude_fallback_is_truthfully_non_phoneme_accurate():
    script = RHIANNON_TIMING_SCRIPT
    assert "createMediaElementSource(audio)" in script
    assert "createAnalyser()" in script
    assert "getByteTimeDomainData" in script
    assert "lip_sync_mode:'amplitude_fallback'" in script
    assert "precise_timing:false" in script
    assert "phoneme_accurate:false" in script
    assert "const viseme=rms>=.035?'aa':'sil'" in script
    assert "Math.random" not in script
    contract = timing_consumption_contract()
    assert contract["fallback"]["phoneme_accurate"] is False
    assert contract["fallback"]["mouth_shapes"] == ["sil", "aa"]
    assert contract["boundaries"]["timing_generation_owned_here"] is False


def test_timing_script_route_is_private_no_store_and_nosniff():
    app = FastAPI()
    app.include_router(timing_router)
    response = TestClient(app).get("/aura-intelligence/rhiannon-voice-timing.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "RhiannonTimingHost" in response.text


def test_voice_workspace_injects_timing_between_turn_state_and_voice_layers():
    app = FastAPI()
    app.add_middleware(AuraVoiceConversationMiddleware)

    @app.get("/aura-intelligence")
    def workspace():
        return HTMLResponse("<html><body><main>Rhiannon</main></body></html>")

    response = TestClient(app).get("/aura-intelligence")
    turn = "<script src='/aura-intelligence/rhiannon-turn-state.js'></script>"
    timing = "<script src='/aura-intelligence/rhiannon-voice-timing.js'></script>"
    fallback = "<script src='/aura-intelligence/voice-conversation.js'></script>"
    realtime = "<script src='/aura-intelligence/realtime-voice.js'></script>"
    assert response.text.index(turn) < response.text.index(timing) < response.text.index(fallback) < response.text.index(realtime)


def test_audio_only_tts_path_starts_and_stops_amplitude_fallback_without_fake_precision():
    assert "window.RhiannonTimingHost" in VOICE_SCRIPT
    assert "startAmplitudeFallback?.(activeAudio,jobId)" in VOICE_SCRIPT
    assert "timingHost()?.stop" in VOICE_SCRIPT
    assert "startPrecise" not in VOICE_SCRIPT
    assert "precise phoneme/viseme frames remain unavailable until a timing-capable runtime supplies them" in VOICE_SCRIPT
