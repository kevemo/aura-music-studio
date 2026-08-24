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
