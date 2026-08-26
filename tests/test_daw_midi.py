from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.daw_midi import (
    MIDI_DAW_NAV,
    MidiCC,
    MidiDocument,
    MidiNote,
    MidiPitchBend,
    MidiTransformRequest,
    _create_clip,
    _public_clip,
    _safe_midi_path,
    _save_document,
    read_midi_document,
    router,
    transform_midi_document,
    write_midi_document,
)
from aura_music_studio.session import StudioSession


def test_midi_round_trip_preserves_notes_velocity_cc_and_pitch_bend(tmp_path):
    path = tmp_path / "editable.mid"
    original = MidiDocument(
        name="Pulsar Chords",
        notes=[
            MidiNote(pitch=60, start_beat=0.0, duration_beats=1.0, velocity=82, channel=0),
            MidiNote(pitch=64, start_beat=0.5, duration_beats=1.5, velocity=101, channel=2),
            MidiNote(pitch=67, start_beat=2.25, duration_beats=0.5, velocity=64, channel=0),
        ],
        cc=[
            MidiCC(beat=0.0, control=1, value=12, channel=0),
            MidiCC(beat=1.5, control=11, value=108, channel=2),
        ],
        pitch_bend=[
            MidiPitchBend(beat=0.75, value=2048, channel=0),
            MidiPitchBend(beat=2.0, value=-1024, channel=2),
        ],
    )

    write_midi_document(path, original, bpm=128.0)
    loaded = read_midi_document(path)

    assert loaded.name == "Pulsar Chords"
    assert [(n.pitch, n.velocity, n.channel) for n in loaded.notes] == [
        (60, 82, 0),
        (64, 101, 2),
        (67, 64, 0),
    ]
    assert [n.start_beat for n in loaded.notes] == pytest.approx([0.0, 0.5, 2.25], abs=1 / 480)
    assert [n.duration_beats for n in loaded.notes] == pytest.approx([1.0, 1.5, 0.5], abs=1 / 480)
    assert [(p.control, p.value, p.channel) for p in loaded.cc] == [(1, 12, 0), (11, 108, 2)]
    assert [p.beat for p in loaded.cc] == pytest.approx([0.0, 1.5], abs=1 / 480)
    assert [(p.value, p.channel) for p in loaded.pitch_bend] == [(2048, 0), (-1024, 2)]
    assert [p.beat for p in loaded.pitch_bend] == pytest.approx([0.75, 2.0], abs=1 / 480)


def test_midi_transform_transposes_clamps_velocity_and_quantizes_non_destructively():
    source = MidiDocument(
        name="Human Guide",
        notes=[
            MidiNote(pitch=2, start_beat=0.31, duration_beats=0.61, velocity=4),
            MidiNote(pitch=124, start_beat=1.37, duration_beats=0.87, velocity=124),
        ],
    )
    transformed = transform_midi_document(
        source,
        MidiTransformRequest(
            transpose_semitones=12,
            velocity_delta=20,
            quantize_grid_beats=0.25,
            quantize_strength=1.0,
            quantize_duration=True,
        ),
    )

    assert [n.pitch for n in transformed.notes] == [14, 127]
    assert [n.velocity for n in transformed.notes] == [24, 127]
    assert [n.start_beat for n in transformed.notes] == [0.25, 1.25]
    assert [n.duration_beats for n in transformed.notes] == [0.5, 0.75]
    assert source.notes[0].pitch == 2
    assert source.notes[0].start_beat == 0.31


def test_safe_midi_path_is_project_confined_and_extension_limited(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    inside = project / "input" / "midi" / "guide.mid"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"midi")
    outside = tmp_path / "outside.mid"
    outside.write_bytes(b"outside")

    assert _safe_midi_path(project, "input/midi/guide.mid") == inside.resolve()
    with pytest.raises(ValueError, match="escapes"):
        _safe_midi_path(project, "../outside.mid")
    with pytest.raises(ValueError, match=".mid"):
        _safe_midi_path(project, "input/midi/guide.json", must_exist=False)
    with pytest.raises(ValueError, match="escapes"):
        _safe_midi_path(project, outside)


def test_midi_clip_stays_project_relative_and_browser_payload_hides_source_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "input" / "midi" / "editable.mid"
    document = MidiDocument(
        name="Editable Melody",
        notes=[MidiNote(pitch=72, start_beat=0.0, duration_beats=2.0, velocity=90)],
    )
    write_midi_document(source, document, bpm=120.0)
    session = StudioSession(name="MIDI Test", bpm=120.0)
    session.add_track("Master", "master")

    track, clip = _create_clip(project, session, name="Editable Melody", track_id=None, source=source)
    _save_document(project, session, clip, document)
    payload = _public_clip("midi-test", track, clip, document)

    assert track.role == "midi"
    assert clip.kind == "midi"
    assert clip.source == "input/midi/editable.mid"
    assert clip.duration == pytest.approx(1.0)
    assert clip.metadata["symbolic_guide_only"] is True
    assert payload["source_path_exposed"] is False
    assert "source" not in payload
    assert str(tmp_path) not in str(payload)
    assert payload["summary"]["note_count"] == 1
    assert payload["editor_url"].startswith("/midi-editor?project=midi-test")


def test_midi_routes_are_production_mounted_and_truthful():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/midi-editor" in paths
    assert "/daw/mixer-ui.js" in paths
    assert "/projects/{project_name}/daw/midi" in paths
    assert "/projects/{project_name}/daw/midi/{clip_id}" in paths
    assert "/projects/{project_name}/daw/midi/{clip_id}/transform" in paths
    assert "/projects/{project_name}/daw/midi/{clip_id}/use-as-guide" in paths

    app_source = Path("app.py").read_text(encoding="utf-8")
    assert "from aura_music_studio.daw_midi import router as daw_midi_router" in app_source
    assert "app.include_router(daw_midi_router)" in app_source

    module_source = Path("aura_music_studio/daw_midi.py").read_text(encoding="utf-8")
    assert '"symbolic_guide_only": True' in module_source
    assert '"audio_rendered": False' in module_source
    assert "connected real music renderer" in module_source


def test_midi_editor_note_placement_is_scroll_safe_and_daw_has_pro_shortcut():
    source = Path("aura_music_studio/daw_midi.py").read_text(encoding="utf-8")
    # getBoundingClientRect already includes the scroller offset. Adding scrollLeft/scrollTop
    # here would double-count the scroll and place notes in the wrong bar/pitch.
    assert "x=e.clientX-r.left-44,y=e.clientY-r.top" in source
    assert "parentElement.scrollLeft" not in source
    assert "parentElement.scrollTop" not in source

    assert "pulsarMidiNav" in MIDI_DAW_NAV
    assert "🎹 MIDI Piano Roll" in MIDI_DAW_NAV
    assert "FLAGS.multitrack" in MIDI_DAW_NAV
    assert "currentProject" in MIDI_DAW_NAV
