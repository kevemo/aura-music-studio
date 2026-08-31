import json
from pathlib import Path

import pytest

from aura_music_studio.creative_renderers import ComfyUIRenderer, renderer_states


def test_comfyui_api_workflow_template_preserves_native_types(tmp_path: Path, monkeypatch):
    workflow = {
        "1": {
            "class_type": "ExampleNode",
            "inputs": {
                "text": "{{prompt}}",
                "seed": "{{seed}}",
                "width": "{{width}}",
                "label": "PFH-{{project_name}}",
            },
        }
    }
    (tmp_path / "image.json").write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("AURA_COMFYUI_WORKFLOW_DIR", str(tmp_path))
    monkeypatch.setenv("AURA_COMFYUI_IMAGE_WORKFLOW", "image.json")

    renderer = ComfyUIRenderer("image")
    assert renderer.configured is True
    prepared = renderer.prepare_workflow({
        "prompt": "cosmic album cover",
        "seed": 42,
        "width": 1024,
        "project_name": "release-one",
    })
    inputs = prepared["1"]["inputs"]
    assert inputs["text"] == "cosmic album cover"
    assert inputs["seed"] == 42
    assert inputs["width"] == 1024
    assert inputs["label"] == "PFH-release-one"


def test_workflow_filename_cannot_escape_configured_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("AURA_COMFYUI_WORKFLOW_DIR", str(tmp_path))
    monkeypatch.setenv("AURA_COMFYUI_VIDEO_WORKFLOW", "../secret.json")
    renderer = ComfyUIRenderer("video")
    assert renderer.configured is False
    with pytest.raises(ValueError, match="safe .json filename"):
        renderer.workflow_path()


def test_collect_outputs_is_generic_across_image_and_video_channels():
    history = {
        "prompt-1": {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "cover.png", "subfolder": "", "type": "output"},
                    ]
                },
                "21": {
                    "gifs": [
                        {"filename": "music_video.mp4", "subfolder": "video", "type": "output"},
                        {"filename": "music_video.mp4", "subfolder": "video", "type": "output"},
                    ]
                },
            }
        }
    }
    outputs = ComfyUIRenderer.collect_outputs(history, "prompt-1")
    assert [(item.filename, item.channel) for item in outputs] == [
        ("cover.png", "images"),
        ("music_video.mp4", "gifs"),
    ]


def test_renderer_state_is_truthful_when_not_configured(monkeypatch):
    monkeypatch.delenv("AURA_COMFYUI_URL", raising=False)
    monkeypatch.delenv("AURA_COMFYUI_IMAGE_WORKFLOW", raising=False)
    monkeypatch.delenv("AURA_COMFYUI_VIDEO_WORKFLOW", raising=False)
    states = renderer_states(probe=False)
    assert states["image"]["configured"] is False
    assert states["video"]["configured"] is False
    assert states["image"]["connected"] is False
    assert states["video"]["connected"] is False


class _FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, snapshots, posts, *args, **kwargs):
        self.snapshots = snapshots
        self.posts = posts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, **kwargs):
        assert url.endswith("/queue")
        assert self.snapshots, "unexpected queue inspection"
        return _FakeResponse(self.snapshots.pop(0))

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return _FakeResponse({})


def _install_fake_client(monkeypatch, snapshots, posts):
    monkeypatch.setattr(
        "aura_music_studio.creative_renderers.httpx.Client",
        lambda *args, **kwargs: _FakeClient(snapshots, posts, *args, **kwargs),
    )


def test_cancel_pending_render_deletes_only_requested_prompt(monkeypatch):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    snapshots = [
        {
            "queue_running": [],
            "queue_pending": [[1, "prompt-1", {"workflow": "private"}, {}, []]],
        },
        {"queue_running": [], "queue_pending": []},
    ]
    posts = []
    _install_fake_client(monkeypatch, snapshots, posts)

    cancellation = ComfyUIRenderer("image").cancel("prompt-1")

    assert cancellation.state == "cancelled_pending"
    assert posts == [
        ("http://127.0.0.1:8188/queue", {"delete": ["prompt-1"]}),
    ]


def test_cancel_running_render_uses_prompt_scoped_interrupt(monkeypatch):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    snapshots = [
        {
            "queue_running": [[0, "prompt-2", {"workflow": "private"}, {}, []]],
            "queue_pending": [],
        }
    ]
    posts = []
    _install_fake_client(monkeypatch, snapshots, posts)

    cancellation = ComfyUIRenderer("video").cancel("prompt-2")

    assert cancellation.state == "cancelled_running"
    assert posts == [
        ("http://127.0.0.1:8188/interrupt", {"prompt_id": "prompt-2"}),
    ]
    assert all(payload is not None for _, payload in posts)


def test_cancel_pending_render_handles_queue_to_running_race(monkeypatch):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    snapshots = [
        {
            "queue_running": [],
            "queue_pending": [[1, "prompt-race", {}, {}, []]],
        },
        {
            "queue_running": [[0, "prompt-race", {}, {}, []]],
            "queue_pending": [],
        },
    ]
    posts = []
    _install_fake_client(monkeypatch, snapshots, posts)

    cancellation = ComfyUIRenderer("image").cancel("prompt-race")

    assert cancellation.state == "cancelled_running"
    assert posts == [
        ("http://127.0.0.1:8188/queue", {"delete": ["prompt-race"]}),
        ("http://127.0.0.1:8188/interrupt", {"prompt_id": "prompt-race"}),
    ]


@pytest.mark.parametrize(
    "prompt_id",
    [
        "bad prompt id",
        "../../queue",
        "prompt?clear=true",
        "prompt#fragment",
        "/absolute/path",
        "prompt%2Fhistory",
    ],
)
def test_cancel_rejects_unsafe_prompt_id_before_network(monkeypatch, prompt_id):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    with pytest.raises(ValueError, match="Invalid ComfyUI prompt id"):
        ComfyUIRenderer("image").cancel(prompt_id)


def test_history_rejects_path_like_prompt_id_before_network(monkeypatch):
    monkeypatch.setenv("AURA_COMFYUI_URL", "http://127.0.0.1:8188")
    with pytest.raises(ValueError, match="Invalid ComfyUI prompt id"):
        ComfyUIRenderer("video").history("../queue")
