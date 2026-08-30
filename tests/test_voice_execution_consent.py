from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_music_studio import harmony, voice
from aura_music_studio.rights import RightsLedger, VoiceProfile, authorize_voice_profile


def _make_profile(tmp_path: Path, *, allowed_uses: list[str] | None = None) -> tuple[Path, RightsLedger, VoiceProfile]:
    rights_root = tmp_path / "project" / ".aura_rights"
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"voice-reference")
    ledger = RightsLedger(rights_root)
    profile = VoiceProfile(
        name="Consent Test Voice",
        owner_label="Test Owner",
        reference_files=[str(reference)],
        consent_confirmed=True,
        consent_statement="I explicitly consent to these approved voice uses.",
        verification_state="attested",
        verification_method="consent_statement_attestation",
        allowed_uses=allowed_uses or ["singing", "backing_harmony", "voice_conversion"],
    )
    return rights_root, ledger, ledger.save_voice(profile)


def test_authorize_voice_profile_reloads_authoritative_ledger(tmp_path: Path):
    rights_root, ledger, profile = _make_profile(tmp_path)
    admitted = ledger.get_voice(profile.id)
    admitted.assert_usable("voice_conversion")

    ledger.revoke_voice(profile.id, "Owner withdrew consent")

    with pytest.raises(PermissionError, match="revoked"):
        authorize_voice_profile(rights_root, admitted.id, "voice_conversion")


def test_voice_conversion_active_profile_runs_with_fresh_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rights_root, _, profile = _make_profile(tmp_path)
    source = tmp_path / "lead.wav"
    source.write_bytes(b"lead")
    output = tmp_path / "converted.wav"
    captured: dict[str, str] = {}

    def fake_run(_command, *, env, check):
        assert check is True
        captured.update(env)
        Path(env["AURA_VOICE_OUTPUT"]).write_bytes(b"converted")

    monkeypatch.setenv("AURA_SEEDVC_CMD", "fake-seedvc")
    monkeypatch.delenv("AURA_RVC_CMD", raising=False)
    monkeypatch.setattr(voice.subprocess, "run", fake_run)

    result = voice.convert_singing_voice(
        source,
        output,
        rights_root=rights_root,
        voice_profile_id=profile.id,
        similarity=0.95,
    )

    assert result == output
    assert output.read_bytes() == b"converted"
    payload = json.loads(captured["AURA_VOICE_PROFILE"])
    assert payload["id"] == profile.id
    assert captured["AURA_VOICE_SIMILARITY"] == str(profile.similarity_limit)


def test_voice_conversion_revoked_after_admission_never_runs_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rights_root, ledger, profile = _make_profile(tmp_path)
    admitted = ledger.get_voice(profile.id)
    admitted.assert_usable("voice_conversion")
    ledger.revoke_voice(profile.id, "Owner withdrew consent after admission")
    source = tmp_path / "lead.wav"
    source.write_bytes(b"lead")
    output = tmp_path / "converted.wav"
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("converter must not run after consent revocation")

    monkeypatch.setenv("AURA_SEEDVC_CMD", "fake-seedvc")
    monkeypatch.setattr(voice.subprocess, "run", forbidden_run)

    with pytest.raises(PermissionError, match="revoked"):
        voice.convert_singing_voice(
            source,
            output,
            rights_root=rights_root,
            voice_profile_id=profile.id,
        )

    assert called is False
    assert not output.exists()


def test_voice_conversion_disallowed_purpose_never_runs_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rights_root, _, profile = _make_profile(tmp_path, allowed_uses=["backing_harmony"])
    source = tmp_path / "lead.wav"
    source.write_bytes(b"lead")
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("converter must not run for a disallowed purpose")

    monkeypatch.setenv("AURA_SEEDVC_CMD", "fake-seedvc")
    monkeypatch.setattr(voice.subprocess, "run", forbidden_run)

    with pytest.raises(PermissionError, match="voice_conversion"):
        voice.convert_singing_voice(
            source,
            tmp_path / "converted.wav",
            rights_root=rights_root,
            voice_profile_id=profile.id,
        )

    assert called is False


def test_harmony_render_active_profile_uses_fresh_authoritative_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rights_root, _, profile = _make_profile(tmp_path)
    midi = tmp_path / "harmony.mid"
    lyrics = tmp_path / "lyrics.txt"
    midi.write_bytes(b"midi")
    lyrics.write_text("hello", encoding="utf-8")
    output = tmp_path / "harmony.wav"
    captured: dict[str, str] = {}

    def fake_run(_command, *, env, check):
        assert check is True
        captured.update(env)
        Path(env["AURA_OUTPUT"]).write_bytes(b"harmony")

    monkeypatch.setenv("AURA_DIFFSINGER_CMD", "fake-diffsinger")
    monkeypatch.setattr(harmony.subprocess, "run", fake_run)

    result = harmony.render_harmony_voice(
        midi,
        lyrics,
        output,
        rights_root=rights_root,
        voice_profile_id=profile.id,
    )

    assert result == output
    payload = json.loads(captured["AURA_VOICE_PROFILE"])
    assert payload["id"] == profile.id
    assert payload["verification_state"] == "attested"


def test_harmony_revoked_after_admission_never_runs_synthesizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rights_root, ledger, profile = _make_profile(tmp_path)
    admitted = ledger.get_voice(profile.id)
    admitted.assert_usable("backing_harmony")
    ledger.revoke_voice(profile.id, "Owner withdrew consent after admission")
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("synthesizer must not run after consent revocation")

    monkeypatch.setenv("AURA_DIFFSINGER_CMD", "fake-diffsinger")
    monkeypatch.setattr(harmony.subprocess, "run", forbidden_run)

    with pytest.raises(PermissionError, match="revoked"):
        harmony.render_harmony_voice(
            tmp_path / "harmony.mid",
            tmp_path / "lyrics.txt",
            tmp_path / "harmony.wav",
            rights_root=rights_root,
            voice_profile_id=profile.id,
        )

    assert called is False


def test_harmony_profile_id_requires_authoritative_rights_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, _, profile = _make_profile(tmp_path)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("synthesizer must not run without authoritative rights storage")

    monkeypatch.setenv("AURA_DIFFSINGER_CMD", "fake-diffsinger")
    monkeypatch.setattr(harmony.subprocess, "run", forbidden_run)

    with pytest.raises(PermissionError, match="rights storage"):
        harmony.render_harmony_voice(
            tmp_path / "harmony.mid",
            tmp_path / "lyrics.txt",
            tmp_path / "harmony.wav",
            voice_profile_id=profile.id,
        )

    assert called is False
