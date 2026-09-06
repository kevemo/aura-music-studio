from __future__ import annotations

import shutil

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.assets import AssetLibrary
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.rights import RightsLedger, VoiceProfile
from aura_music_studio.session import StudioSession
from aura_music_studio import voice_conversion_workflow as workflow


def _tone(path, seconds: float = 1.1, sample_rate: int = 24000):
    t = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    sf.write(path, 0.2 * np.sin(2 * np.pi * 220 * t), sample_rate, subtype="PCM_16")
    return path


def _authorised_profile(ledger: RightsLedger, reference, name: str = "Authorised Singer") -> VoiceProfile:
    return ledger.save_voice(
        VoiceProfile(
            name=name,
            owner_label="Voice Owner",
            reference_files=[str(reference)],
            consent_confirmed=True,
            consent_statement="I explicitly authorise this private profile for voice conversion in my project.",
            subject_relationship="self",
            verification_state="attested",
            allowed_uses=["voice_conversion", "singing", "backing_harmony"],
        )
    )


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    StudioSession(name="Voice Conversion Test").save(project / "aura_session.json")
    return project


def test_generation_creates_private_candidate_without_mutating_daw(tmp_path, monkeypatch):
    project = _project(tmp_path)
    source_file = _tone(tmp_path / "lead.wav")
    ref_file = _tone(tmp_path / "reference.wav")

    token = set_current_user_id("member-a")
    try:
        source_asset = AssetLibrary(project).ingest(source_file, kind="audio")
        profile = _authorised_profile(RightsLedger(project / ".aura_rights"), ref_file)

        def fake_convert(source, output, **kwargs):
            shutil.copy2(source, output)
            return output

        monkeypatch.setattr(workflow, "convert_singing_voice", fake_convert)
        before = StudioSession.load(project / "aura_session.json")
        candidate = workflow.generate_voice_conversion_candidate(
            project,
            source_asset_id=source_asset.id,
            voice_profile_id=profile.id,
            similarity=0.75,
            pitch_shift=2,
        )
        after = StudioSession.load(project / "aura_session.json")
    finally:
        reset_current_user_id(token)

    assert candidate.state == "ready"
    assert candidate.metadata["audition_required"] is True
    assert candidate.metadata["authoritative_daw_mutated"] is False
    assert candidate.candidate_path.startswith("work/voice_conversion/audio/")
    assert before.model_dump() == after.model_dump()
    assert (project / candidate.candidate_path).is_file()


def test_commit_rechecks_consent_and_blocks_revoked_profile(tmp_path, monkeypatch):
    project = _project(tmp_path)
    source_file = _tone(tmp_path / "lead.wav")
    ref_file = _tone(tmp_path / "reference.wav")

    token = set_current_user_id("member-a")
    try:
        source_asset = AssetLibrary(project).ingest(source_file, kind="audio")
        ledger = RightsLedger(project / ".aura_rights")
        profile = _authorised_profile(ledger, ref_file)

        def fake_convert(source, output, **kwargs):
            shutil.copy2(source, output)
            return output

        monkeypatch.setattr(workflow, "convert_singing_voice", fake_convert)
        candidate = workflow.generate_voice_conversion_candidate(
            project,
            source_asset_id=source_asset.id,
            voice_profile_id=profile.id,
            similarity=0.8,
            pitch_shift=0,
        )
        ledger.revoke_voice(profile.id, "Consent withdrawn after audition")
        with pytest.raises(PermissionError, match="revoked"):
            workflow.commit_voice_conversion_candidate(project, candidate_id=candidate.id)
    finally:
        reset_current_user_id(token)

    reloaded = workflow.get_candidate(project, candidate.id)
    assert reloaded.state == "ready"
    assert StudioSession.load(project / "aura_session.json").tracks == []


def test_commit_creates_real_asset_editable_daw_clip_revision_and_provenance(tmp_path, monkeypatch):
    project = _project(tmp_path)
    source_file = _tone(tmp_path / "lead.wav", seconds=1.25)
    ref_file = _tone(tmp_path / "reference.wav")

    token = set_current_user_id("member-a")
    try:
        source_asset = AssetLibrary(project).ingest(source_file, kind="audio")
        profile = _authorised_profile(RightsLedger(project / ".aura_rights"), ref_file)

        def fake_convert(source, output, **kwargs):
            shutil.copy2(source, output)
            return output

        monkeypatch.setattr(workflow, "convert_singing_voice", fake_convert)
        candidate = workflow.generate_voice_conversion_candidate(
            project,
            source_asset_id=source_asset.id,
            voice_profile_id=profile.id,
            similarity=0.7,
            pitch_shift=-1,
        )
        committed = workflow.commit_voice_conversion_candidate(
            project,
            candidate_id=candidate.id,
            start_seconds=3.5,
            track_name="Converted Lead — Take 1",
        )
    finally:
        reset_current_user_id(token)

    assert committed.state == "committed"
    assert committed.pre_commit_revision_id
    assert committed.committed_asset_id
    assert committed.committed_track_id
    assert committed.committed_clip_id

    session = StudioSession.load(project / "aura_session.json")
    track = session.find_track(committed.committed_track_id)
    clip = next(item for item in track.clips if item.id == committed.committed_clip_id)
    assert clip.kind == "audio"
    assert clip.start == 3.5
    assert clip.duration > 1.0
    assert clip.metadata["voice_conversion"] is True
    assert clip.metadata["consent_rechecked_at_commit"] is True
    assert clip.metadata["voice_profile_id"] == profile.id
    assert clip.metadata["source_asset_id"] == source_asset.id
    assert (project / clip.source).is_file()

    committed_asset = AssetLibrary(project).get(committed.committed_asset_id)
    assert "voice_conversion" in committed_asset.tags
    assert committed_asset.path == clip.source
    assert (project / "work" / "revisions" / committed.pre_commit_revision_id / "revision.json").is_file()
    assert session.generation_history[-1]["action"] == "voice_conversion_commit"
    assert session.generation_history[-1]["consent_rechecked_at_commit"] is True


def test_candidate_tamper_is_detected_before_commit(tmp_path, monkeypatch):
    project = _project(tmp_path)
    source_file = _tone(tmp_path / "lead.wav")
    ref_file = _tone(tmp_path / "reference.wav")

    token = set_current_user_id("member-a")
    try:
        source_asset = AssetLibrary(project).ingest(source_file, kind="audio")
        profile = _authorised_profile(RightsLedger(project / ".aura_rights"), ref_file)

        def fake_convert(source, output, **kwargs):
            shutil.copy2(source, output)
            return output

        monkeypatch.setattr(workflow, "convert_singing_voice", fake_convert)
        candidate = workflow.generate_voice_conversion_candidate(
            project,
            source_asset_id=source_asset.id,
            voice_profile_id=profile.id,
            similarity=0.8,
            pitch_shift=0,
        )
        _tone(project / candidate.candidate_path, seconds=1.5, sample_rate=22050)
        with pytest.raises(RuntimeError, match="changed after generation"):
            workflow.commit_voice_conversion_candidate(project, candidate_id=candidate.id)
    finally:
        reset_current_user_id(token)
