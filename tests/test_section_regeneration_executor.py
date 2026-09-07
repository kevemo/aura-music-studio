from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio import section_regeneration as sr
from aura_music_studio import song_edit_executor as executor
from aura_music_studio.mixer import selected_audio_clips
from aura_music_studio.session import Clip, StudioSession, Track
from aura_music_studio.song_dna import InstrumentLayer, SongDNA, SongDNAStore, SongEditDirective, SongSection


def _project_state(tmp_path: Path):
    project = tmp_path / "song"
    project.mkdir()
    audio = project / "audio"
    audio.mkdir()
    sources = {}
    for name in ("vocal", "guitar", "strings"):
        path = audio / f"{name}.wav"
        path.write_bytes(f"{name}-source".encode())
        sources[name] = path

    session = StudioSession(
        name="Editable song",
        tracks=[
            Track(
                id="track-vocal",
                name="Lead vocal",
                role="vocals",
                clips=[
                    Clip(
                        id="clip-vocal",
                        name="Lead vocal",
                        kind="audio",
                        source="audio/vocal.wav",
                        start=0.0,
                        duration=30.0,
                        metadata={"real_audio": True, "committed": True},
                    )
                ],
            ),
            Track(
                id="track-guitar",
                name="Guitar",
                role="guitar",
                clips=[
                    Clip(
                        id="clip-guitar",
                        name="Guitar",
                        kind="audio",
                        source="audio/guitar.wav",
                        start=0.0,
                        duration=30.0,
                        metadata={"real_audio": True, "committed": True},
                    )
                ],
            ),
            Track(
                id="track-strings",
                name="Strings",
                role="strings",
                clips=[
                    Clip(
                        id="clip-strings",
                        name="Strings",
                        kind="audio",
                        source="audio/strings.wav",
                        start=0.0,
                        duration=30.0,
                        metadata={"real_audio": True, "committed": True},
                    )
                ],
            ),
        ],
    )
    session_path = project / "aura_session.json"
    session.save(session_path)

    layers = [
        InstrumentLayer(id="layer-vocal", role="vocals", label="Lead vocal", track_id="track-vocal"),
        InstrumentLayer(id="layer-guitar", role="guitar", label="Guitar", track_id="track-guitar"),
        InstrumentLayer(id="layer-strings", role="strings", label="Strings", track_id="track-strings", locked=True),
    ]
    directive = SongEditDirective(
        id="edit-section",
        action="regenerate_section",
        instruction="Make the chorus bigger while preserving the song identity.",
        target_ids=["sec-chorus"],
        # This is the legacy planner representation of preserve_instruments=True. The multitrack
        # executor must translate it into identity preservation, not skip every instrument.
        preserve_ids=[layer.id for layer in layers],
        metadata={"local_regeneration_required": True},
    )
    dna = SongDNA(
        project_name="song",
        title="Song",
        sections=[SongSection(id="sec-chorus", name="Chorus", start_seconds=10.0, end_seconds=20.0)],
        instruments=layers,
        directives=[directive],
    )
    SongDNAStore(project).save(dna)
    return project, session_path


def _install_generation_fakes(monkeypatch):
    def fake_region_take(source, request, work_dir, take_number):
        work_dir.mkdir(parents=True, exist_ok=True)
        result = work_dir / "candidate.wav"
        result.write_bytes(b"candidate")
        return result

    def fake_render(session, project, output, work_dir):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mix")
        return output

    monkeypatch.setattr(sr, "generate_region_take", fake_region_take)
    monkeypatch.setattr(executor, "render_session", fake_render)


def _install_commit_fakes(monkeypatch, project: Path, *, fail_quality: bool = False):
    manifest = SimpleNamespace(
        mix=SimpleNamespace(
            mastering_preset="streaming",
            target_lufs=-14.0,
            true_peak_db=-1.0,
            mastering_reference=None,
        ),
        renderer=SimpleNamespace(minimum_quality_score=0.9),
    )

    class FakeWorkspace:
        def __init__(self, root):
            self.project = Path(root)
            self.output_dir = self.project / "output"

        def load_manifest(self):
            return manifest

        def resolve_asset(self, value):
            return self.project / str(value)

    def fake_master(source, destination, **kwargs):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"master")
        return destination, {"ok": True}

    def fake_quality(*args, **kwargs):
        return {"technical_gate_passed": True, "perceptual_review_required": True}

    def fake_enforce(report):
        if fail_quality:
            raise RuntimeError("release quality rejected")
        return report

    monkeypatch.setattr(executor, "ProjectWorkspace", FakeWorkspace)
    monkeypatch.setattr(executor, "master", fake_master)
    monkeypatch.setattr(executor, "build_release_quality_report", fake_quality)
    monkeypatch.setattr(executor, "enforce_release_quality", fake_enforce)
    monkeypatch.setattr(executor, "create_revision", lambda *args, **kwargs: None)


def test_generate_section_candidate_creates_multitrack_batch_and_single_audition_preview(monkeypatch, tmp_path):
    project, session_path = _project_state(tmp_path)
    original = session_path.read_bytes()
    _install_generation_fakes(monkeypatch)

    result = executor.generate_candidate(project, "edit-section")

    assert result.state == "ready"
    assert result.candidate_kind == "multitrack_section_regeneration_preview"
    assert Path(result.candidate_path).is_file()
    assert result.metadata["candidate_count"] == 2
    assert set(result.metadata["track_ids"]) == {"track-vocal", "track-guitar"}
    assert session_path.read_bytes() == original

    directive = SongDNAStore(project).load().directives[0]
    assert directive.status == "ready"
    batch = sr.SectionCandidateBatch.model_validate(directive.metadata["section_candidate_batch"])
    assert batch.preserve_instrument_identity is True
    assert {item.track_id for item in batch.tracks} == {"track-vocal", "track-guitar"}
    assert "track-strings" not in {item.track_id for item in batch.tracks}


def test_commit_section_batch_persists_all_tracks_only_after_release_gate(monkeypatch, tmp_path):
    project, session_path = _project_state(tmp_path)
    _install_generation_fakes(monkeypatch)
    executor.generate_candidate(project, "edit-section")
    _install_commit_fakes(monkeypatch, project)

    result = executor.commit_candidate(project, "edit-section")

    assert result.state == "complete"
    assert result.committed is True
    assert len(result.metadata["committed_clip_ids"]) == 2
    assert set(result.metadata["committed_track_ids"]) == {"track-vocal", "track-guitar"}

    saved = StudioSession.load(session_path)
    vocal = selected_audio_clips(saved.find_track("track-vocal"))
    guitar = selected_audio_clips(saved.find_track("track-guitar"))
    strings = selected_audio_clips(saved.find_track("track-strings"))
    assert len(vocal) == 1 and vocal[0].metadata["section_regeneration_batch"] is True
    assert len(guitar) == 1 and guitar[0].metadata["section_regeneration_batch"] is True
    assert len(strings) == 1 and strings[0].id == "clip-strings"

    dna = SongDNAStore(project).load()
    directive = dna.directives[0]
    assert directive.status == "complete"
    assert len(directive.metadata["committed_clip_ids"]) == 2
    by_id = {layer.id: layer for layer in dna.instruments}
    assert by_id["layer-vocal"].source_ref == vocal[0].source
    assert by_id["layer-guitar"].source_ref == guitar[0].source
    assert (project / "output" / "Aura_Final_Master.wav").is_file()


def test_failed_release_gate_never_overwrites_authoritative_session(monkeypatch, tmp_path):
    project, session_path = _project_state(tmp_path)
    _install_generation_fakes(monkeypatch)
    executor.generate_candidate(project, "edit-section")
    authoritative_before = session_path.read_bytes()
    _install_commit_fakes(monkeypatch, project, fail_quality=True)

    with pytest.raises(RuntimeError, match="release quality rejected"):
        executor.commit_candidate(project, "edit-section")

    assert session_path.read_bytes() == authoritative_before
    assert not (project / "output" / "Aura_Final_Master.wav").exists()
    directive = SongDNAStore(project).load().directives[0]
    assert directive.status == "ready"


def test_commit_rejects_stale_section_timing(monkeypatch, tmp_path):
    project, _session_path = _project_state(tmp_path)
    _install_generation_fakes(monkeypatch)
    executor.generate_candidate(project, "edit-section")
    store = SongDNAStore(project)
    dna = store.load()
    dna.sections[0].end_seconds = 21.0
    store.save(dna)
    _install_commit_fakes(monkeypatch, project)

    with pytest.raises(RuntimeError, match="Section timing changed"):
        executor.commit_candidate(project, "edit-section")
