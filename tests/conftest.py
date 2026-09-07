from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.shared_sky_internal_media import internal_media


# Wave 2 intentionally made first-party playback fail closed unless a real media runtime,
# FFmpeg capability, and contribution ingest are available. These pre-Wave-2 transport tests
# exercise state/idempotency/token behaviour rather than FFmpeg itself, so they receive a
# deterministic in-process media adapter instead of weakening the production readiness gate.
# Exact test-name scoping prevents the adapter from masking Wave 2 fail-closed/runtime tests.
_LEGACY_TRANSPORT_TESTS_REQUIRING_MEDIA_RUNTIME = {
    "test_broadcast_state_machine_and_idempotent_internal_start_stop",
    "test_internal_playback_can_start_when_external_destination_is_unavailable",
    "test_playback_descriptor_is_short_lived_and_authorized",
    "test_capacity_snapshot_reports_measured_transport_pressure",
    "test_playback_token_verifier_enforces_signature_and_binding",
}


@pytest.fixture(autouse=True)
def _shared_sky_wave2_legacy_media_runtime(request, monkeypatch):
    if request.node.name not in _LEGACY_TRANSPORT_TESTS_REQUIRING_MEDIA_RUNTIME:
        return

    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root="/test/shared-sky-media",
            recording_root_configured=False,
            active_jobs=1,
            runtime_mode="pytest-deterministic-media-adapter",
        ),
    )

    jobs_by_broadcast: dict[str, list[dict]] = {}

    def start_hls(*, broadcast_id, input_url, profile=None):
        del input_url, profile
        jobs = [
            {
                "job_id": f"pytest_hls_{broadcast_id}",
                "broadcast_id": broadcast_id,
                "kind": "hls",
                "rendition": "720p",
                "pid": 4242,
                "output_path": f"/test/shared-sky-media/{broadcast_id}/720p/index.m3u8",
            }
        ]
        jobs_by_broadcast[broadcast_id] = jobs
        return jobs

    def stop_broadcast(broadcast_id, kinds=None):
        jobs = jobs_by_broadcast.pop(broadcast_id, [])
        if kinds and "hls" not in kinds:
            return []
        return [dict(job, returncode=0) for job in jobs]

    monkeypatch.setattr(internal_media, "start_hls", start_hls)
    monkeypatch.setattr(
        internal_media,
        "state",
        lambda job_id: {
            "job_id": job_id,
            "running": True,
            "managed": True,
            "pid": 4242,
            "returncode": None,
        },
    )
    monkeypatch.setattr(internal_media, "stop_broadcast", stop_broadcast)
