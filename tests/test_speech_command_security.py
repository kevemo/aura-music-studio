from __future__ import annotations

import subprocess

import pytest

from aura_music_studio.speech import AuraSpeechService


def test_configured_speech_command_uses_argv_without_shell(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("aura_music_studio.speech.subprocess.run", fake_run)

    result = AuraSpeechService._run_template(
        'speech-provider --input "{input}" --output {output}',
        {
            "input": "/tmp/project with spaces/input.wav",
            "output": "/tmp/project with spaces/output.wav",
        },
    )

    assert result.stdout == "ok"
    assert captured["argv"] == [
        "speech-provider",
        "--input",
        "/tmp/project with spaces/input.wav",
        "--output",
        "/tmp/project with spaces/output.wav",
    ]
    assert captured["shell"] is False
    assert captured["check"] is True
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_replacement_text_cannot_gain_shell_semantics(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("aura_music_studio.speech.subprocess.run", fake_run)

    AuraSpeechService._run_template(
        'speech-provider --text "{text}" --output {output}',
        {
            "text": "hello; $(touch /tmp/not-executed) | still plain text",
            "output": "/tmp/output.wav",
        },
    )

    assert captured["argv"] == [
        "speech-provider",
        "--text",
        "hello; $(touch /tmp/not-executed) | still plain text",
        "--output",
        "/tmp/output.wav",
    ]
    assert captured["shell"] is False


@pytest.mark.parametrize(
    "template",
    [
        "speech-provider {input}; touch /tmp/escape",
        "speech-provider {input} | cat",
        "speech-provider {input} && other-provider",
        "speech-provider {input} > /tmp/output",
        "speech-provider `id`",
        "speech-provider $(id)",
        "speech-provider {input}\nother-provider",
    ],
)
def test_shell_syntax_in_configured_template_is_rejected_before_execution(monkeypatch, template):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("subprocess must not run for unsafe configured templates")

    monkeypatch.setattr("aura_music_studio.speech.subprocess.run", forbidden_run)

    with pytest.raises(RuntimeError, match="forbidden shell syntax"):
        AuraSpeechService._run_template(template, {"input": "/tmp/input.wav"})


def test_malformed_command_quoting_fails_closed_before_execution(monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("subprocess must not run for malformed configured templates")

    monkeypatch.setattr("aura_music_studio.speech.subprocess.run", forbidden_run)

    with pytest.raises(RuntimeError, match="invalid quoting"):
        AuraSpeechService._run_template('speech-provider --input "{input}', {"input": "/tmp/input.wav"})


def test_unknown_placeholder_fails_closed_before_execution(monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("subprocess must not run for unsupported placeholders")

    monkeypatch.setattr("aura_music_studio.speech.subprocess.run", forbidden_run)

    with pytest.raises(RuntimeError, match="unsupported placeholder"):
        AuraSpeechService._run_template(
            "speech-provider --input {input} --tenant {tenant}",
            {"input": "/tmp/input.wav"},
        )
