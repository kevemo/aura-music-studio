from __future__ import annotations

from starlette.requests import Request

import aura_music_studio.shared_sky_live_neighbor_wave4 as wave4


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/shared-sky/live/api/watch/live-1/browser-playback-session",
            "raw_path": b"/shared-sky/live/api/watch/live-1/browser-playback-session",
            "query_string": b"",
            "headers": [(b"host", b"command.example")],
            "client": ("127.0.0.1", 12345),
            "server": ("command.example", 443),
        }
    )


def test_rendition_and_caption_projection_keeps_only_same_origin_media():
    request = _request()

    renditions = wave4._safe_renditions(
        request,
        [
            {"name": "720p", "manifest_url": "/shared-sky/media/live-1/720.m3u8"},
            {"name": "480p", "profile": {"url": "https://command.example/shared-sky/media/live-1/480.m3u8"}},
            {"name": "foreign", "manifest_url": "https://cdn.example/foreign.m3u8"},
            {"name": "credential", "manifest_url": "https://user:secret@command.example/private.m3u8"},
            {"name": "metadata-only", "profile": {"height": 360}},
        ],
    )
    captions = wave4._safe_captions(
        request,
        [
            {"label": "English", "language": "en", "url": "/shared-sky/media/live-1/en.vtt", "default": True},
            {"label": "Spanish", "srclang": "es", "src": "https://command.example/shared-sky/media/live-1/es.vtt"},
            {"label": "Foreign", "language": "xx", "url": "https://captions.example/xx.vtt"},
            {"label": "Script", "url": "javascript:alert(1)"},
        ],
    )

    assert renditions == [
        {"name": "720p", "manifest_url": "/shared-sky/media/live-1/720.m3u8"},
        {"name": "480p", "manifest_url": "https://command.example/shared-sky/media/live-1/480.m3u8"},
    ]
    assert captions == [
        {
            "src": "/shared-sky/media/live-1/en.vtt",
            "srclang": "en",
            "label": "English",
            "default": True,
        },
        {
            "src": "https://command.example/shared-sky/media/live-1/es.vtt",
            "srclang": "es",
            "label": "Spanish",
            "default": False,
        },
    ]
