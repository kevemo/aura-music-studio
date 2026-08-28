from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from aura_music_studio.creation import CreateSongRequest, build_song_project
from aura_music_studio.mastering import analyze_master
from aura_music_studio.models import MixConfig, ProjectManifest, RendererConfig
from aura_music_studio.release_quality import build_release_quality_report
from aura_music_studio.song_dna import SongDNAStore, create_song_dna


def test_new_song_project_initializes_release_grade_editable_dna(tmp_path):
    project = build_song_project(
        CreateSongRequest(
            title="Editable Release",
            lyrics="First line\nSecond line\n\nChorus one\nChorus two",
            lyrics_rights_confirmed=True,
            genre="pop",
            mood="uplifting",
            instruments=["drums", "bass", "piano"],
            structure="Verse 1, Chorus",
        ),
        tmp_path,
    )
    assert (project / "song_dna.json").is_file()
    dna = SongDNAStore(project).load()
    assert dna.title == "Editable Release"
    assert len(dna.sections) == 2
    assert len(dna.lyric_lines) == 4
    assert {layer.label for layer in dna.instruments} == {"drums", "bass", "piano"}
    assert dna.quality_contract["standard"] == "release_grade_editable_master"

    import yaml
    manifest = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert manifest["renderer"]["minimum_quality_score"] == 0.72
    assert manifest["renderer"]["quality_retries"] == 3
    assert manifest["renderer"]["allow_symbolic_guide_as_final"] is False
    assert "AI warble" in manifest["prompt"]


def test_lyric_line_edit_is_non_destructive_and_updates_source_lyrics(tmp_path):
    project = tmp_path / "song"
    dna = create_song_dna(
        project,
        project_name="song",
        title="Song",
        structure="Verse 1, Chorus",
        lyrics="Old lyric one\nOld lyric two\n\nChorus lyric",
        instruments=["piano", "drums"],
    )
    target = dna.lyric_lines[0]
    store = SongDNAStore(project)
    updated = store.update_lyric_line(target.id, "New lyric one")

    assert updated.version == 2
    changed = next(line for line in updated.lyric_lines if line.id == target.id)
    assert changed.text == "New lyric one"
    assert changed.revision == 2
    assert updated.directives[-1].action == "replace_lyric_line"
    assert target.id in updated.directives[-1].target_ids
    assert (project / "input" / "lyrics.txt").is_file()
    assert "New lyric one" in (project / "input" / "lyrics.txt").read_text(encoding="utf-8")


def test_instrument_and_section_edits_create_local_renderer_directives(tmp_path):
    project = tmp_path / "song"
    dna = create_song_dna(
        project,
        project_name="song",
        title="Song",
        structure="Verse 1, Chorus",
        lyrics="Verse lyric\n\nChorus lyric",
        instruments=["piano", "drums"],
    )
    store = SongDNAStore(project)
    piano = next(layer for layer in dna.instruments if layer.label == "piano")
    verse = dna.sections[0]

    replaced = store.plan_instrument_replacement(piano.id, "fingerpicked acoustic guitar")
    assert replaced.directives[-1].action == "replace_instrument"
    assert replaced.directives[-1].metadata["replacement"] == "fingerpicked acoustic guitar"
    assert replaced.directives[-1].status == "planned"

    regenerated = store.plan_section_regeneration(verse.id, "Make this verse more intimate; keep the chorus unchanged")
    directive = regenerated.directives[-1]
    assert directive.action == "regenerate_section"
    assert verse.id in directive.target_ids
    assert directive.metadata["local_regeneration_required"] is True


def test_release_quality_report_passes_clean_48k_24bit_master(tmp_path):
    project = tmp_path / "song"
    output = project / "output"
    output.mkdir(parents=True)
    create_song_dna(project, project_name="song", title="Song", instruments=["piano"])

    sr = 48000
    seconds = 3
    t = np.arange(sr * seconds, dtype=np.float32) / sr
    mono = 0.08 * np.sin(2 * np.pi * 440.0 * t)
    audio = np.column_stack([mono, mono]).astype(np.float32)
    master = output / "Aura_Final_Master.wav"
    sf.write(master, audio, sr, subtype="PCM_24")
    measured = analyze_master(master)

    manifest = ProjectManifest(
        project_name="song",
        title="Song",
        rights_confirmed=True,
        renderer=RendererConfig(minimum_quality_score=0.7),
        mix=MixConfig(target_lufs=float(measured["integrated_lufs"]), true_peak_db=-1.0),
    )
    report = build_release_quality_report(
        project,
        exports={"master_wav": str(master), "stems": []},
        manifest=manifest,
        render_qc={"passes_basic_integrity": True, "quality_score": 0.9},
    )
    assert report["technical_gate_passed"] is True
    assert report["perceptual_review_required"] is True
    assert report["release_ready"] is False
    assert any("stems" in warning.lower() for warning in report["warnings"])
    assert (output / "release_quality_report.json").is_file()
