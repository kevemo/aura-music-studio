from __future__ import annotations

from types import SimpleNamespace

import pytest

import aura_music_studio.restoration as restoration
import aura_music_studio.spatial as spatial
import aura_music_studio.speech as speech
import aura_music_studio.tone as tone
from aura_music_studio.command_templates import render_command_argv


def test_render_command_argv_preserves_quoted_and_embedded_placeholder_arguments():
    argv = render_command_argv(
        'tool --input "{input}" --output={output}',
        {"input": "/tmp/source with spaces.wav", "output": "/tmp/out file.wav"},
    )

    assert argv == [
        "tool",
        "--input",
        "/tmp/source with spaces.wav",
        "--output=/tmp/out file.wav",
    ]


def test_runtime_value_cannot_become_shell_syntax_or_extra_arguments():
    malicious = 'hello; touch /tmp/should-not-exist && echo pwned'
    argv = render_command_argv("tool --text {text}", {"text": malicious})

    assert argv == ["tool", "--text", malicious]
    assert len(argv) == 3


@pytest.mark.parametrize(
    "template",
    [
        "tool {input} | helper",
        "tool {input} && helper",
        "tool {input} ; helper",
        "tool {input} > output.txt",
        "tool {input} 2> output.txt",
        "tool {input} &",
    ],
)
def test_unquoted_shell_control_operators_are_rejected(template: str):
    with pytest.raises(ValueError, match="shell pipes, redirects or control operators"):
        render_command_argv(template, {"input": "safe.wav"})


def test_shell_punctuation_inside_a_quoted_direct_argument_is_allowed():
    argv = render_command_argv('python -c "print(\'a;b|c>\')"', {})
    assert argv == ["python", "-c", "print('a;b|c>')"]


def test_unknown_placeholder_fails_closed():
    with pytest.raises(ValueError, match="unsupported placeholders: output"):
        render_command_argv("tool {input} {output}", {"input": "safe.wav"})


def test_invalid_or_empty_templates_fail_closed():
    with pytest.raises(ValueError, match="empty"):
        render_command_argv("   ", {})
    with pytest.raises(ValueError, match="invalid quoting"):
        render_command_argv("tool 'unterminated", {})
    with pytest.raises(ValueError, match="control characters"):
        render_command_argv("tool\nother", {})


def test_runtime_nul_byte_is_rejected():
    with pytest.raises(ValueError, match="NUL byte"):
        render_command_argv("tool {input}", {"input": "bad\x00value"})


def test_restoration_adapter_invokes_subprocess_with_argv_not_shell(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(restoration.subprocess, "run", fake_run)
    restoration.AudioRestorer._run_template(
        'denoise --input "{input}" --output "{output}"',
        {"input": "/tmp/in file.wav", "output": "/tmp/out.wav"},
    )

    assert captured["argv"] == ["denoise", "--input", "/tmp/in file.wav", "--output", "/tmp/out.wav"]
    assert captured["kwargs"] == {"check": True}


def test_speech_adapter_invokes_subprocess_with_argv_not_shell(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(speech.subprocess, "run", fake_run)
    result = speech.AuraSpeechService._run_template(
        "tts --text {text} --output {output}",
        {"text": "hello; still one argument", "output": "/tmp/out.wav"},
    )

    assert result.stdout == "ok"
    assert captured["argv"] == ["tts", "--text", "hello; still one argument", "--output", "/tmp/out.wav"]
    assert captured["kwargs"] == {"check": True, "capture_output": True, "text": True}


def test_tone_adapter_invokes_subprocess_with_argv_not_shell(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tone.subprocess, "run", fake_run)
    tone.NeuralToneProcessor._render(
        "nam --input {input} --model {model} --output {output}",
        {"input": "/tmp/in.wav", "model": "/tmp/model.nam", "output": "/tmp/out.wav"},
    )

    assert captured["argv"] == [
        "nam",
        "--input",
        "/tmp/in.wav",
        "--model",
        "/tmp/model.nam",
        "--output",
        "/tmp/out.wav",
    ]
    assert captured["kwargs"] == {"check": True}


def test_spatial_adapter_invokes_subprocess_with_argv_not_shell(tmp_path, monkeypatch):
    captured = {}
    output = tmp_path / "out.wav"

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        output.write_bytes(b"0" * 2048)
        return SimpleNamespace(returncode=0)

    renderer = spatial.SpatialRenderer()
    renderer.command = "spatial-render --input {input} --output {output} --mode {mode}"
    monkeypatch.setattr(spatial.subprocess, "run", fake_run)
    rendered, report = renderer.immersive(tmp_path / "input.wav", output, mode="binaural")

    assert rendered == output.resolve()
    assert report["engine"] == "configured_local_spatial_renderer"
    assert captured["argv"] == [
        "spatial-render",
        "--input",
        str((tmp_path / "input.wav").resolve()),
        "--output",
        str(output.resolve()),
        "--mode",
        "binaural",
    ]
    assert captured["kwargs"] == {"check": True}
