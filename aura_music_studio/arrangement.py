from __future__ import annotations

from .models import AnalysisResult, ArrangementPlan, ProjectManifest


DEFAULT_INSTRUMENT_BRIEF = {
    "drums": "Acoustic pop-rock kit; humanized kick/snare/hat dynamics; fills only at structural transitions; natural room microphones.",
    "bass": "Warm fingered electric bass; follow harmonic roots with musical passing notes; lock tightly to kick without sounding quantized.",
    "acoustic_guitar": "Steel-string acoustic guitar; physically believable strumming and chord voicings; dynamic down/up strokes.",
    "electric_rhythm": "Two separately performed electric rhythm guitars, left/right; complementary voicings; restrained drive; no copy-paste doubling.",
    "piano": "Grand piano support; avoid simply doubling guitar; voice-lead chord tones and add sparse fills around vocal gaps.",
    "synths": "Subtle analog-style keys and pads; support width and lift without masking lead vocal.",
    "strings": "Natural ensemble swells and sustained voice-leading; strongest in choruses/final build.",
    "percussion": "Tasteful tambourine/shaker accents with human timing; avoid loop-machine repetition.",
}


def build_plan(manifest: ProjectManifest, analysis: AnalysisResult) -> ArrangementPlan:
    bpm = manifest.tempo_bpm or analysis.tempo_bpm
    if not bpm:
        raise ValueError("Aura needs a known tempo or analyzable reference audio.")

    instrument_brief = dict(DEFAULT_INSTRUMENT_BRIEF)
    production = manifest.production
    enabled = {
        "drums": production.realistic_drums,
        "bass": production.fingered_bass,
        "acoustic_guitar": production.acoustic_guitar,
        "electric_rhythm": production.electric_rhythm_guitars,
        "piano": production.piano,
        "synths": production.synths,
        "strings": production.strings,
        "percussion": production.percussion,
    }
    instrument_brief = {k: v for k, v in instrument_brief.items() if enabled[k]}

    backing_vocals = (
        "Wordless human backing singers using ooh/ahh harmony stacks. Keep verses sparse, build thirds/fifths "
        "through pre-choruses, broaden choruses, and create the largest but still supportive stack in the final build. "
        "Never imitate a named singer; leave the center clear for the user's lead vocal."
        if production.wordless_backing_harmonies
        else "No backing vocals."
    )
    counter = (
        "Write an ORIGINAL expressive single-note guitar countermelody across the arrangement. Use chord tones, "
        "scale approach notes, bends/slides/vibrato and phrase-end answers; increase activity in instrumental gaps. "
        "Do not duplicate a copyrighted lead-vocal melody note-for-note."
        if production.original_single_note_countermelody
        else "No countermelody."
    )

    section_text = "; ".join(
        f"{s.name} measures {s.start_measure}-{s.end_measure} energy {s.energy:.2f}"
        for s in manifest.sections
    ) or "Follow the supplied reference/score structure exactly."

    prompt = (
        f"High-end realistic studio backing production. Tempo {float(bpm):g} BPM, meter {manifest.meter}, "
        f"key {manifest.key or analysis.key or 'follow source'}. {section_text}. "
        + " ".join(instrument_brief.values())
        + " " + counter + " " + backing_vocals + " "
        + "Preserve structure and harmonic timing from user-supplied assets. Natural microtiming, velocity, articulation, "
          "room tone, amp/cabinet behavior and performance variation. Avoid General MIDI timbre, mechanical repetition, "
          "EDM drums, clipped mastering, lead vocals, spoken voice and excessive center masking. "
        + manifest.prompt
    ).strip()

    negative = (
        "synthetic MIDI timbre, cheap soundfont, robotic timing, quantized strumming, fake choir, lead singer, spoken voice, "
        "EDM kick, trap hats, distorted master, clipping, mono mix, harsh limiter, copyrighted vocal melody duplication"
    )
    if manifest.negative_prompt:
        negative += ", " + manifest.negative_prompt

    return ArrangementPlan(
        project_name=manifest.project_name,
        tempo_bpm=float(bpm),
        key=manifest.key or analysis.key,
        meter=manifest.meter,
        sections=manifest.sections,
        instrument_brief=instrument_brief,
        backing_vocal_brief=backing_vocals,
        countermelody_brief=counter,
        render_prompt=prompt,
        negative_prompt=negative,
    )
