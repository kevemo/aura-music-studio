from __future__ import annotations

from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class InstrumentType:
    id: str
    label: str
    prompt: str
    family: str
    pro_only: bool = False


CATALOG: dict[str, list[InstrumentType]] = {
    "guitar": [
        InstrumentType("acoustic_steel", "Acoustic — steel string", "natural steel-string acoustic guitar, human pick and fret detail", "guitar"),
        InstrumentType("acoustic_nylon", "Acoustic — nylon/classical", "warm nylon-string classical guitar with natural finger articulation", "guitar"),
        InstrumentType("acoustic_12_string", "Acoustic — 12 string", "shimmering real 12-string acoustic guitar", "guitar", True),
        InstrumentType("electric_clean", "Electric — clean", "clean electric guitar through a polished amp and cabinet", "guitar"),
        InstrumentType("electric_crunch", "Electric — crunch", "dynamic crunchy electric guitar amp tone with pick attack", "guitar"),
        InstrumentType("electric_high_gain", "Electric — high gain", "tight high-gain electric guitar with realistic amp/cab response", "guitar", True),
        InstrumentType("electric_jazz", "Electric — jazz hollow body", "warm hollow-body jazz electric guitar", "guitar", True),
        InstrumentType("baritone", "Baritone electric", "deep baritone electric guitar with controlled low-end", "guitar", True),
        InstrumentType("slide", "Slide / lap steel", "expressive slide or lap-steel guitar with natural glissando", "guitar", True),
    ],
    "bass": [
        InstrumentType("electric_precision", "Electric — Precision style", "round punchy fingered electric bass", "bass"),
        InstrumentType("electric_jazz", "Electric — Jazz style", "articulate electric bass with detailed finger attack", "bass"),
        InstrumentType("pick", "Electric — picked", "tight picked electric bass", "bass"),
        InstrumentType("five_string", "5-string electric", "extended-range five-string electric bass", "bass", True),
        InstrumentType("fretless", "Fretless", "expressive fretless bass with slides and singing sustain", "bass", True),
        InstrumentType("upright", "Upright / double bass", "natural acoustic upright double bass", "bass"),
        InstrumentType("synth", "Synth bass", "controlled modern synth bass designed around the song key and groove", "bass", True),
    ],
    "drums": [
        InstrumentType("studio_pop", "Studio pop kit", "real studio-recorded pop drum kit with human timing and velocity", "drums"),
        InstrumentType("rock", "Rock kit", "large live rock drum kit, acoustic shells and energetic cymbals", "drums"),
        InstrumentType("indie", "Indie kit", "dry characterful indie acoustic drum kit", "drums"),
        InstrumentType("funk", "Funk kit", "tight funky acoustic drums with ghost notes and pocket", "drums"),
        InstrumentType("jazz", "Jazz kit", "natural jazz kit with ride articulation and dynamic snare", "drums"),
        InstrumentType("brushes", "Brush kit", "acoustic drum kit played with brushes", "drums", True),
        InstrumentType("metal", "Metal kit", "tight modern metal kit with fast kick definition and hard-hitting shells", "drums", True),
        InstrumentType("electronic", "Electronic kit", "modern electronic drum kit layered with organic transients", "drums"),
        InstrumentType("trap", "Trap kit", "tight trap drums, 808-compatible kick, crisp hats and snare", "drums", True),
        InstrumentType("lofi", "Lo-fi kit", "dusty lo-fi drums with soft transient shaping and human swing", "drums", True),
    ],
    "keyboard": [
        InstrumentType("grand_piano", "Concert grand piano", "realistic concert grand piano with natural pedal resonance", "keyboard"),
        InstrumentType("upright_piano", "Upright piano", "characterful felt/upright acoustic piano", "keyboard"),
        InstrumentType("electric_piano", "Electric piano", "warm tine electric piano", "keyboard"),
        InstrumentType("organ", "Tonewheel organ", "tonewheel organ with expressive rotary speaker", "keyboard", True),
        InstrumentType("clav", "Clavinet", "funky clavinet with percussive key attack", "keyboard", True),
    ],
    "synth": [
        InstrumentType("analog_pad", "Analog pad", "warm evolving analog synthesizer pad", "synth"),
        InstrumentType("digital_pad", "Digital pad", "glossy wide digital synthesizer pad", "synth"),
        InstrumentType("poly", "Poly synth", "musical polyphonic synthesizer chords", "synth"),
        InstrumentType("mono_lead", "Mono lead synth", "expressive monophonic synthesizer lead", "synth", True),
        InstrumentType("arp", "Arpeggiator", "tempo-synced synthesizer arpeggio locked to the harmony", "synth", True),
        InstrumentType("pluck", "Synth pluck", "short articulate synthesizer pluck", "synth", True),
    ],
    "strings": [
        InstrumentType("ensemble", "String ensemble", "realistic orchestral string ensemble", "strings"),
        InstrumentType("chamber", "Chamber strings", "intimate chamber string section", "strings", True),
        InstrumentType("solo_violin", "Solo violin", "expressive solo violin with bow articulation", "strings", True),
        InstrumentType("solo_cello", "Solo cello", "expressive solo cello with bow articulation", "strings", True),
        InstrumentType("pizzicato", "Pizzicato strings", "realistic pizzicato orchestral strings", "strings", True),
    ],
    "brass": [
        InstrumentType("pop_horns", "Pop horn section", "tight pop horn section with trumpet, trombone and sax-style voicing", "brass"),
        InstrumentType("orchestral", "Orchestral brass", "cinematic orchestral brass section", "brass", True),
        InstrumentType("trumpet", "Solo trumpet", "expressive real trumpet", "brass", True),
        InstrumentType("trombone", "Solo trombone", "expressive real trombone", "brass", True),
    ],
    "woodwinds": [
        InstrumentType("flute", "Flute", "expressive real flute", "woodwinds"),
        InstrumentType("clarinet", "Clarinet", "natural real clarinet", "woodwinds", True),
        InstrumentType("sax", "Saxophone", "expressive real saxophone", "woodwinds", True),
        InstrumentType("orchestral", "Woodwind ensemble", "orchestral woodwind ensemble", "woodwinds", True),
    ],
    "percussion": [
        InstrumentType("shaker_tambourine", "Shaker + tambourine", "human-played shaker and tambourine supporting the groove", "percussion"),
        InstrumentType("congas", "Congas / hand percussion", "natural hand percussion and congas", "percussion"),
        InstrumentType("latin", "Latin percussion", "full Latin percussion layer with human dynamics", "percussion", True),
        InstrumentType("cinematic", "Cinematic percussion", "large cinematic percussion with controlled impacts", "percussion", True),
    ],
    "vocals": [
        InstrumentType("lead_natural", "Natural lead vocal", "natural expressive lead vocal with believable phrasing", "vocals"),
        InstrumentType("backing_pop", "Pop backing harmonies", "tight supportive pop backing harmonies", "backing_vocals"),
        InstrumentType("choir", "Choir / ensemble", "layered human vocal ensemble and choir textures", "backing_vocals", True),
        InstrumentType("wordless", "Wordless ooh/aah", "wordless ooh and aah backing vocal textures", "backing_vocals"),
    ],
}


# Automatic defaults intentionally use only non-Pro instrument types so Base members never
# receive a hidden advanced entitlement simply because of a genre choice. Pro sounds remain
# available explicitly through the switch-board.
DEFAULT_BY_GENRE: dict[str, list[tuple[str, str]]] = {
    "pop": [("drums", "studio_pop"), ("bass", "electric_precision"), ("guitar", "electric_clean"), ("keyboard", "grand_piano"), ("synth", "analog_pad")],
    "rock": [("drums", "rock"), ("bass", "electric_precision"), ("guitar", "electric_crunch"), ("keyboard", "upright_piano")],
    "acoustic": [("drums", "studio_pop"), ("bass", "upright"), ("guitar", "acoustic_steel"), ("keyboard", "grand_piano")],
    "country": [("drums", "studio_pop"), ("bass", "electric_precision"), ("guitar", "acoustic_steel"), ("guitar", "electric_clean")],
    "jazz": [("drums", "jazz"), ("bass", "upright"), ("guitar", "electric_clean"), ("keyboard", "grand_piano")],
    "funk": [("drums", "funk"), ("bass", "electric_jazz"), ("guitar", "electric_clean"), ("keyboard", "electric_piano")],
    "metal": [("drums", "rock"), ("bass", "pick"), ("guitar", "electric_crunch")],
    "electronic": [("drums", "electronic"), ("bass", "electric_precision"), ("synth", "poly"), ("synth", "analog_pad")],
    "hiphop": [("drums", "electronic"), ("bass", "electric_precision"), ("keyboard", "electric_piano"), ("synth", "analog_pad")],
    "cinematic": [("percussion", "shaker_tambourine"), ("strings", "ensemble"), ("brass", "pop_horns"), ("keyboard", "grand_piano")],
}


class InstrumentSwitch(BaseModel):
    family: str
    type_id: str
    enabled: bool = True
    prominence: float = Field(default=0.65, ge=0.0, le=1.0)
    custom_direction: str = ""


def get_type(family: str, type_id: str) -> InstrumentType:
    family = family.strip().lower()
    type_id = type_id.strip().lower()
    for item in CATALOG.get(family, []):
        if item.id == type_id:
            return item
    raise KeyError(f"Unknown instrument selection: {family}/{type_id}")


def selection_prompt(selections: list[InstrumentSwitch]) -> tuple[str, list[str]]:
    prompts: list[str] = []
    tracks: list[str] = []
    for selection in selections:
        if not selection.enabled:
            continue
        item = get_type(selection.family, selection.type_id)
        prompts.append(
            f"{item.label}: {item.prompt}; prominence {selection.prominence:.2f}"
            + (f"; {selection.custom_direction}" if selection.custom_direction else "")
        )
        role = "keyboard" if item.family == "keyboard" else item.family
        if role not in tracks:
            tracks.append(role)
    return ". ".join(prompts), tracks


def defaults_for_genre(genre: str) -> list[InstrumentSwitch]:
    key = (genre or "pop").strip().lower()
    rows = DEFAULT_BY_GENRE.get(key, DEFAULT_BY_GENRE["pop"])
    return [InstrumentSwitch(family=f, type_id=t) for f, t in rows]


def public_catalog() -> dict[str, list[dict]]:
    return {family: [asdict(item) for item in items] for family, items in CATALOG.items()}
