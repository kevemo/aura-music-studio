from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ProductionPreset:
    genre: str
    default_bpm: tuple[int, int]
    instruments: tuple[str, ...]
    arrangement: str
    drum_style: str
    bass_style: str
    vocal_style: str
    mix_notes: str
    master_preset: str


PRESETS: dict[str, ProductionPreset] = {
    "pop": ProductionPreset(
        "pop", (95, 128),
        ("acoustic/electric drums", "bass", "piano/keys", "electric guitar", "synth", "backing vocals"),
        "tight verses, rising pre-chorus, wide hook-heavy chorus, contrast bridge, elevated final chorus",
        "punchy acoustic/electronic hybrid with humanized fills", "melodic supportive bass with controlled sub",
        "clear intimate lead, stacked chorus harmonies", "bright vocal-forward commercial stereo mix", "pop"),
    "rock": ProductionPreset(
        "rock", (90, 150),
        ("live drums", "electric bass", "double-tracked rhythm guitars", "lead guitar", "piano/organ", "backing vocals"),
        "dynamic live-band build with larger choruses and guitar-led transitions",
        "real kit, room ambience, ghost notes and fills", "fingered/picked bass locked to kick",
        "natural forceful lead with group/stack harmonies", "wide guitars, punchy center rhythm section, natural room", "rock"),
    "acoustic": ProductionPreset(
        "acoustic", (70, 120),
        ("steel-string acoustic guitar", "piano", "upright/electric bass", "brush/light drums", "strings", "backing vocals"),
        "organic sparse opening, gradually layered choruses, breathing room around vocal",
        "brushes/light kit or percussion", "warm restrained bass",
        "close natural vocal and subtle harmonies", "open dynamics, realistic room and low processing", "acoustic"),
    "country": ProductionPreset(
        "country", (80, 140),
        ("acoustic guitar", "electric guitar", "bass", "live drums", "piano", "pedal steel/fiddle", "backing vocals"),
        "story-led verses, memorable choruses, instrumental fills between vocal phrases",
        "live pocket with natural snare", "root/fifth movement with tasteful passing notes",
        "forward conversational vocal with close harmonies", "warm Nashville-style clarity and transient detail", "acoustic"),
    "r&b": ProductionPreset(
        "r&b", (65, 105),
        ("drums", "sub/electric bass", "Rhodes", "guitar", "pads", "vocal stacks"),
        "space-led verses, pre-hook lift, layered chorus, vocal/ad-lib outro",
        "laid-back pocket with microtiming", "deep smooth bass with slides",
        "intimate expressive lead, rich thirds/sevenths and ad-libs", "warm low mids, silky top end, vocal depth", "streaming"),
    "soul": ProductionPreset(
        "soul", (65, 115),
        ("live drums", "electric bass", "Rhodes/Hammond", "guitar", "horns/strings", "choir/backing vocals"),
        "live ensemble dynamics and call-and-response choruses",
        "human pocket with ghost notes", "melodic Motown/soul movement",
        "emotive lead with gospel-influenced harmony stacks", "vintage warmth with modern clarity", "ballad"),
    "hip-hop": ProductionPreset(
        "hip-hop", (65, 105),
        ("drum kit", "808/sub bass", "keys", "sample textures", "synth", "FX"),
        "loop-driven sections with strategic drops, beat switches and hook contrast",
        "hard transient groove with swing", "sub-led 808 or electric bass",
        "dry present lead with doubles/ad-libs", "strong low end, center vocal, controlled limiting", "hiphop"),
    "edm": ProductionPreset(
        "edm", (118, 150),
        ("electronic drums", "sub bass", "synth bass", "pads", "leads", "plucks", "FX", "vocals"),
        "intro, tension build, drop, breakdown, rebuild, final drop/outro",
        "club kick with programmed percussion", "sidechained sub/synth bass",
        "processed lead/chops with wide stacks", "wide high-energy mix with mono-compatible sub", "electronic"),
    "metal": ProductionPreset(
        "metal", (90, 200),
        ("acoustic drums", "bass guitar", "multi-tracked distorted guitars", "lead guitar", "vocals"),
        "riff-led form, dynamic breakdowns, solos and high-impact transitions",
        "fast realistic kit with double-kick where appropriate", "tight bass reinforcing guitar rhythm",
        "aggressive/clean vocals as requested, controlled doubles", "tight low end, dense guitars, preserved attack", "rock"),
    "folk": ProductionPreset(
        "folk", (70, 125),
        ("acoustic guitar", "mandolin/banjo", "upright bass", "hand percussion", "fiddle", "vocal harmonies"),
        "song-led organic arrangement with ensemble growth",
        "light percussion or natural kit", "upright/rooted bass",
        "natural storytelling lead and group harmonies", "organic room, restrained compression", "acoustic"),
    "jazz": ProductionPreset(
        "jazz", (70, 180),
        ("acoustic drums", "upright bass", "piano", "guitar", "horns"),
        "head, solos, comping interaction, return to head/outro",
        "swing/brush/live dynamics", "walking or sparse upright bass",
        "natural vocal if used", "wide dynamic range and realistic room", "cinematic"),
    "blues": ProductionPreset(
        "blues", (60, 135),
        ("live drums", "bass", "electric guitar", "piano/Hammond", "harmonica", "vocals"),
        "riff/call-and-response form with expressive instrumental answers",
        "shuffle or straight live groove", "rooted walking bass",
        "raw expressive lead and simple harmonies", "tube-like warmth and guitar dynamics", "rock"),
    "cinematic": ProductionPreset(
        "cinematic", (50, 130),
        ("orchestra", "piano", "hybrid percussion", "synth textures", "choir", "sound design"),
        "long-form dynamic arcs, motifs, tension/release and scene transitions",
        "orchestral/hybrid percussion", "orchestral low strings or synth sub",
        "choir/vocal texture as requested", "high dynamic range, depth and front-to-back staging", "cinematic"),
    "ambient": ProductionPreset(
        "ambient", (45, 100),
        ("pads", "textural synths", "piano", "guitar textures", "field ambience", "subtle percussion"),
        "slow evolving layers and spectral movement rather than verse/chorus dependency",
        "minimal or absent", "subtle sustained low foundation",
        "ethereal processed vocal texture if requested", "large depth, low transient density, preserved dynamics", "cinematic"),
    "reggae": ProductionPreset(
        "reggae", (70, 100),
        ("drums", "deep electric bass", "skank guitar", "organ/keys", "percussion", "horns", "backing vocals"),
        "groove-first verses and singalong chorus responses",
        "one-drop/rockers/steppers as requested", "deep melodic bass carrying harmonic motion",
        "relaxed lead and response harmonies", "bass-forward warm mix with crisp offbeats", "streaming"),
    "latin": ProductionPreset(
        "latin", (85, 135),
        ("acoustic/electric percussion", "bass", "guitar", "piano", "brass", "synth", "vocals"),
        "rhythmic verses, lift into memorable chorus, percussion/brass transitions",
        "genre-specific syncopated percussion", "syncopated melodic bass",
        "expressive lead and tight harmonies", "rhythm clarity, warm mids and lively stereo percussion", "pop"),
    "indie": ProductionPreset(
        "indie", (80, 145),
        ("live drums", "bass", "guitars", "keys/synth", "textures", "vocals"),
        "characterful contrast, imperfect human dynamics and distinctive transitions",
        "human live pocket", "melodic supportive bass",
        "character-led natural vocal with creative doubles", "less-polished character while retaining professional translation", "streaming"),
}


def get_preset(genre: str) -> ProductionPreset:
    key = genre.strip().lower().replace("&", "&")
    aliases = {"r&b": "r&b", "rnb": "r&b", "edm": "edm", "hip hop": "hip-hop", "hiphop": "hip-hop"}
    key = aliases.get(key, key)
    return PRESETS.get(key, PRESETS["pop"])


def preset_dict(genre: str) -> dict:
    return asdict(get_preset(genre))
