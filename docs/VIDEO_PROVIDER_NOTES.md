# Video provider integration notes — August 2026

The Live Sound Studio video layer is deliberately model-agnostic.

## OpenAI video

The OpenAI Videos API supports prompt-based video generation and optional image references. The current adapter submits real asynchronous video jobs and records the provider job ID. Supported duration and output-size constraints are normalized by the Live Sound Studio adapter.

## Runway

Runway exposes text-to-video, image-to-video and video-to-video generation APIs. The Live Sound Studio adapter submits real generation jobs and preserves provider/model metadata for provenance and later status polling/download.

## Local/self-hosted

`AURA_VIDEO_RENDER_CMD` allows a local or self-hosted renderer to participate in the same routing system. The command receives prompt, mode, aspect ratio, duration, project ID and reference metadata through environment variables and must write a real MP4 to `AURA_VIDEO_OUTPUT`.

## Non-negotiable behavior

The system must never represent a placeholder, storyboard, still image or metadata-only response as a finished generated video. If no real video renderer is available, generation fails clearly.
