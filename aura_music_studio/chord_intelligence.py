from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from .project import ProjectWorkspace
from .revisions import create_revision, revision_root
from .song_dna import SongDNAStore, ensure_song_dna_from_manifest
from .tenant_storage import project_path

router = APIRouter(tags=["Chord Intelligence"])

MAX_CHORD_EVENTS = 512
MAX_CHORD_SECONDS = 60.0 * 60.0 * 12.0
MAX_SYMBOL_CHARS = 32

ReharmonizeMode = Literal["simplify", "sophisticate"]
ReharmonizeStyle = Literal["pop", "jazz", "gospel", "cinematic"]
ChordSource = Literal["manual", "generated", "detected", "imported"]

_NOTE_TO_PC = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4, "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7,
    "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}
_KEY_RE = re.compile(r"^\s*([A-Ga-g])([#b]?)(?:\s*(major|maj|minor|min|m))?\s*$")
_CHORD_RE = re.compile(
    r"^\s*([A-Ga-g])([#b]?)(maj9|min9|m9|maj7|min7|m7|dim7|dim|aug|sus2|sus4|add9|m6|6|9|7|min|m)?(?:/([A-Ga-g])([#b]?))?\s*$"
)
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")
_MAJOR_DEGREES = (
    (1, ""), (1, "#"), (2, ""), (3, "b"), (3, ""), (4, ""),
    (4, "#"), (5, ""), (6, "b"), (6, ""), (7, "b"), (7, ""),
)
_MINOR_DEGREES = (
    (1, ""), (2, "b"), (2, ""), (3, ""), (3, "#"), (4, ""),
    (4, "#"), (5, ""), (6, ""), (6, "#"), (7, ""), (7, "#"),
)
_MAJOR_SCALE = {0, 2, 4, 5, 7, 9, 11}
_MINOR_SCALE = {0, 2, 3, 5, 7, 8, 10}


class ChordEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"chord_{uuid4().hex}", min_length=1, max_length=96)
    symbol: str = Field(min_length=1, max_length=MAX_SYMBOL_CHARS)
    start_seconds: float = Field(ge=0.0, le=MAX_CHORD_SECONDS)
    end_seconds: float = Field(gt=0.0, le=MAX_CHORD_SECONDS)
    section_id: str | None = Field(default=None, max_length=128)
    source: ChordSource = "manual"
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Chord end_seconds must be greater than start_seconds")
        object.__setattr__(self, "symbol", normalize_chord_symbol(self.symbol))
        return self


class ChordDescriptor(BaseModel):
    symbol: str
    root: str
    quality: str
    bass: str | None = None
    pitch_class: int
    key: str | None = None
    mode: Literal["major", "minor"] | None = None
    in_key: bool | None = None
    scale_degree: int | None = None
    roman_numeral: str | None = None
    nashville_number: str | None = None


class ChordProgressionUpdate(BaseModel):
    events: list[ChordEvent] = Field(default_factory=list, max_length=MAX_CHORD_EVENTS)
    key: str | None = Field(default=None, max_length=24)


class ChordCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=MAX_SYMBOL_CHARS)
    start_seconds: float = Field(ge=0.0, le=MAX_CHORD_SECONDS)
    end_seconds: float = Field(gt=0.0, le=MAX_CHORD_SECONDS)
    section_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Chord end_seconds must be greater than start_seconds")
        return self


class ChordReplacementRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=MAX_SYMBOL_CHARS)


class ReharmonizeRequest(BaseModel):
    mode: ReharmonizeMode
    style: ReharmonizeStyle = "pop"
    key: str | None = Field(default=None, max_length=24)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_note(letter: str, accidental: str = "") -> str:
    return letter.upper() + accidental


def _normalize_quality(raw: str | None) -> str:
    value = (raw or "").lower()
    return {"min": "m", "min7": "m7", "min9": "m9"}.get(value, value)


def parse_chord_symbol(symbol: str) -> tuple[str, str, str | None]:
    raw = (symbol or "").strip()
    if not raw or len(raw) > MAX_SYMBOL_CHARS or any(ord(ch) < 32 for ch in raw):
        raise ValueError("Invalid chord symbol")
    match = _CHORD_RE.fullmatch(raw)
    if not match:
        raise ValueError(
            "Unsupported chord symbol. Use a note plus a bounded common quality such as C, Cm, C7, Cmaj7, Cm7, Cdim, Csus4, Cadd9 or C/E."
        )
    root = _canonical_note(match.group(1), match.group(2) or "")
    quality = _normalize_quality(match.group(3))
    bass = _canonical_note(match.group(4), match.group(5) or "") if match.group(4) else None
    if root not in _NOTE_TO_PC or (bass is not None and bass not in _NOTE_TO_PC):
        raise ValueError("Unsupported chord note spelling")
    return root, quality, bass


def normalize_chord_symbol(symbol: str) -> str:
    root, quality, bass = parse_chord_symbol(symbol)
    return f"{root}{quality}{f'/{bass}' if bass else ''}"


def parse_key(key: str) -> tuple[str, Literal["major", "minor"]]:
    match = _KEY_RE.fullmatch((key or "").strip())
    if not match:
        raise ValueError("Key must be a tonic such as C, F# minor, Bb major or Cm")
    tonic = _canonical_note(match.group(1), match.group(2) or "")
    if tonic not in _NOTE_TO_PC:
        raise ValueError("Unsupported key tonic")
    token = (match.group(3) or "major").lower()
    mode: Literal["major", "minor"] = "minor" if token in {"minor", "min", "m"} else "major"
    return tonic, mode


def describe_chord(symbol: str, key: str | None = None) -> ChordDescriptor:
    root, quality, bass = parse_chord_symbol(symbol)
    descriptor = ChordDescriptor(
        symbol=normalize_chord_symbol(symbol), root=root, quality=quality, bass=bass, pitch_class=_NOTE_TO_PC[root]
    )
    if not key:
        return descriptor
    tonic, mode = parse_key(key)
    interval = (_NOTE_TO_PC[root] - _NOTE_TO_PC[tonic]) % 12
    degree, accidental = (_MAJOR_DEGREES if mode == "major" else _MINOR_DEGREES)[interval]
    in_key = interval in (_MAJOR_SCALE if mode == "major" else _MINOR_SCALE)
    roman = _ROMAN[degree - 1]
    if quality.startswith("m") and not quality.startswith("maj"):
        roman = roman.lower()
    if quality.startswith("dim"):
        roman = roman.lower() + "°"
    elif quality == "aug":
        roman += "+"
    descriptor.key = f"{tonic} {mode}"
    descriptor.mode = mode
    descriptor.in_key = in_key
    descriptor.scale_degree = degree
    descriptor.roman_numeral = accidental + roman
    descriptor.nashville_number = accidental + str(degree)
    return descriptor


def _simplified_quality(quality: str) -> str:
    if quality in {"m", "m6", "m7", "m9"}:
        return "m"
    if quality in {"dim", "dim7"}:
        return "dim"
    if quality == "aug":
        return "aug"
    return ""


def _sophisticated_quality(quality: str, style: ReharmonizeStyle) -> str:
    minor = quality in {"m", "m6", "m7", "m9"}
    diminished = quality in {"dim", "dim7"}
    dominant = quality in {"7", "9"}
    if diminished:
        return "dim7"
    if style == "jazz":
        return "m9" if minor else "9" if dominant else "maj9"
    if style == "gospel":
        return "m7" if minor else "9" if dominant else "add9"
    if style == "cinematic":
        return "m7" if minor else "7" if dominant else "add9"
    return "m7" if minor else "7" if dominant else "add9"


def reharmonize_symbol(symbol: str, *, mode: ReharmonizeMode, style: ReharmonizeStyle = "pop") -> str:
    root, quality, _bass = parse_chord_symbol(symbol)
    new_quality = _simplified_quality(quality) if mode == "simplify" else _sophisticated_quality(quality, style)
    return normalize_chord_symbol(f"{root}{new_quality}")


def validate_progression(events: list[ChordEvent]) -> list[ChordEvent]:
    if len(events) > MAX_CHORD_EVENTS:
        raise ValueError(f"Chord progression is limited to {MAX_CHORD_EVENTS} events")
    ordered = sorted(
        (ChordEvent.model_validate(event) for event in events),
        key=lambda item: (item.start_seconds, item.end_seconds, item.id),
    )
    seen: set[str] = set()
    for event in ordered:
        if event.id in seen:
            raise ValueError(f"Duplicate chord id: {event.id}")
        seen.add(event.id)
    return ordered


def reharmonize_progression(
    events: list[ChordEvent], *, mode: ReharmonizeMode, style: ReharmonizeStyle = "pop"
) -> list[ChordEvent]:
    output: list[ChordEvent] = []
    for event in validate_progression(events):
        changed = event.model_copy(deep=True)
        before = changed.symbol
        changed.symbol = reharmonize_symbol(changed.symbol, mode=mode, style=style)
        changed.source = "generated"
        changed.metadata = {
            **changed.metadata,
            "reharmonized_from": before,
            "reharmonization_mode": mode,
            "reharmonization_style": style,
            "reharmonized_at": _now(),
            "deterministic": True,
        }
        output.append(changed)
    return output


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(name: str) -> SongDNAStore:
    project = _project(name)
    store = SongDNAStore(project)
    if not store.path.is_file():
        manifest = ProjectWorkspace(project).load_manifest()
        ensure_song_dna_from_manifest(project, manifest.model_dump(mode="json"))
    return store


def _load_progression(store: SongDNAStore) -> list[ChordEvent]:
    raw = store.load().metadata.get("chord_progression") or []
    if not isinstance(raw, list):
        raise ValueError("Song DNA chord progression is invalid")
    return validate_progression([ChordEvent.model_validate(item) for item in raw])


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_song_dna(project: Path, *, label: str) -> dict:
    """Add Song DNA to the shared checkpoint so this edit is fully restorable."""
    manifest = create_revision(project, label=label, reason="song_dna_chord_edit", actor="Rhiannon")
    source = project / "song_dna.json"
    if not source.is_file():
        return manifest
    folder = revision_root(project) / str(manifest["id"])
    target = folder / "song_dna.json"
    shutil.copy2(source, target)
    row = {"path": "song_dna.json", "sha256": _hash_file(source), "bytes": source.stat().st_size}
    manifest["files"] = [item for item in manifest.get("files", []) if item.get("path") != "song_dna.json"] + [row]
    domains = dict(manifest.get("domains") or {})
    domains["music_song_dna"] = {"files": 1, "bytes": row["bytes"], "paths": ["song_dna.json"]}
    manifest["domains"] = domains
    manifest["song_dna_included"] = True
    manifest["cross_editor_checkpoint"] = True
    (folder / "revision.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _persist_progression(store: SongDNAStore, events: list[ChordEvent], *, key: str | None, operation: str) -> dict:
    dna = store.load()
    effective_key = key or dna.key
    if effective_key:
        tonic, mode = parse_key(effective_key)
        effective_key = f"{tonic} {mode}"
    validated = validate_progression(events)
    revision = _checkpoint_song_dna(store.project, label=f"Before chord {operation}")
    dna.metadata["chord_progression"] = [event.model_dump(mode="json") for event in validated]
    dna.metadata["chord_intelligence"] = {
        "schema_version": 1,
        "updated_at": _now(),
        "operation": operation,
        "key": effective_key,
        "event_count": len(validated),
        "editable": True,
        "audio_rendered": False,
        "midi_rewritten": False,
    }
    dna.version += 1
    store.save(dna)
    return {
        "events": [event.model_dump(mode="json") for event in validated],
        "analysis": [describe_chord(event.symbol, effective_key).model_dump(mode="json") for event in validated],
        "song_dna_version": dna.version,
        "revision_id": revision["id"],
        "key": effective_key,
        "audio_rendered": False,
        "midi_rewritten": False,
        "non_destructive": True,
        "detail": "Chord intelligence was updated and checkpointed. Linked accompaniment audio/MIDI was not regenerated by this operation.",
    }


def _current_payload(store: SongDNAStore) -> dict:
    dna = store.load()
    events = _load_progression(store)
    effective_key = dna.metadata.get("chord_intelligence", {}).get("key") or dna.key
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "analysis": [describe_chord(event.symbol, effective_key).model_dump(mode="json") for event in events],
        "song_dna_version": dna.version,
        "key": effective_key,
        "editable": True,
        "audio_rendered": False,
        "midi_rewritten": False,
    }


@router.get("/projects/{project_name}/song-dna/chords")
def get_project_chords(project_name: str):
    try:
        return _current_payload(_store(project_name))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/projects/{project_name}/song-dna/chords")
def replace_project_chord_map(project_name: str, request: ChordProgressionUpdate):
    try:
        return _persist_progression(_store(project_name), request.events, key=request.key, operation="replace_progression")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/song-dna/chords")
def add_project_chord(project_name: str, request: ChordCreateRequest):
    try:
        store = _store(project_name)
        events = _load_progression(store)
        events.append(
            ChordEvent(
                symbol=request.symbol,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                section_id=request.section_id,
                source="manual",
            )
        )
        return _persist_progression(store, events, key=None, operation="add_chord")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/projects/{project_name}/song-dna/chords/{chord_id}")
def replace_project_chord(project_name: str, chord_id: str, request: ChordReplacementRequest):
    try:
        store = _store(project_name)
        events = _load_progression(store)
        target = next((event for event in events if event.id == chord_id), None)
        if target is None:
            raise HTTPException(404, "Chord event not found")
        before = target.symbol
        target.symbol = normalize_chord_symbol(request.symbol)
        target.source = "manual"
        target.metadata = {**target.metadata, "replaced_from": before, "replaced_at": _now()}
        return _persist_progression(store, events, key=None, operation="replace_chord")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/projects/{project_name}/song-dna/chords/{chord_id}")
def delete_project_chord(project_name: str, chord_id: str):
    try:
        store = _store(project_name)
        events = _load_progression(store)
        remaining = [event for event in events if event.id != chord_id]
        if len(remaining) == len(events):
            raise HTTPException(404, "Chord event not found")
        return _persist_progression(store, remaining, key=None, operation="delete_chord")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/song-dna/chords/reharmonize")
def reharmonize_project_chords(project_name: str, request: ReharmonizeRequest):
    try:
        store = _store(project_name)
        changed = reharmonize_progression(_load_progression(store), mode=request.mode, style=request.style)
        result = _persist_progression(
            store, changed, key=request.key, operation=f"reharmonize_{request.mode}_{request.style}"
        )
        result["deterministic_reharmonization"] = True
        result["provider_invoked"] = False
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _chord_rows(store: SongDNAStore) -> str:
    payload = _current_payload(store)
    labels = {item["symbol"]: item for item in payload["analysis"]}
    rows: list[str] = []
    for event in payload["events"]:
        analysis = labels.get(event["symbol"], {})
        chord_id = html.escape(str(event["id"]), quote=True)
        symbol = html.escape(str(event["symbol"]), quote=True)
        rows.append(
            "<tr>"
            f"<td>{event['start_seconds']:.2f}s–{event['end_seconds']:.2f}s</td>"
            f"<td><form class='replaceChord' data-id='{chord_id}'><label class='sr' for='symbol-{chord_id}'>Chord symbol</label>"
            f"<input id='symbol-{chord_id}' name='symbol' value='{symbol}' maxlength='{MAX_SYMBOL_CHARS}' required> "
            "<button type='submit'>Replace</button></form></td>"
            f"<td>{html.escape(str(analysis.get('roman_numeral') or '—'))}</td>"
            f"<td>{html.escape(str(analysis.get('nashville_number') or '—'))}</td>"
            f"<td><button type='button' class='deleteChord' data-id='{chord_id}'>Delete</button></td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='5'>No chord events yet. Add the first chord below.</td></tr>"


@router.get("/song-editor/{project_name}/chords", response_class=HTMLResponse, include_in_schema=False)
def chord_editor_portal(project_name: str, request: Request):
    store = _store(project_name)
    payload = _current_payload(store)
    encoded = quote(project_name, safe="")
    rows = _chord_rows(store)
    key_label = html.escape(str(payload.get("key") or "Not set"))
    return HTMLResponse(
        f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Chord Studio · {html.escape(project_name)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#11131a;color:#f5f5f7}}main{{max-width:1050px;margin:auto;padding:24px}}a{{color:#cbb7ff}}table{{width:100%;border-collapse:collapse;margin:18px 0}}th,td{{padding:10px;border-bottom:1px solid #343746;text-align:left}}input,select,button{{font:inherit;padding:8px;border-radius:8px;border:1px solid #555}}button{{cursor:pointer}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}.card{{background:#191c25;padding:16px;border-radius:14px;margin:16px 0}}.sr{{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}}#status{{min-height:1.5em}}
</style></head><body><main>
<p><a href='/song-editor/{encoded}'>← Song Editor</a></p><h1>Chord Studio</h1>
<p>Editable Song DNA chord map. Current key: <strong>{key_label}</strong>. Chord changes are checkpointed before mutation. This editor does not claim to regenerate audio or MIDI until that renderer path is connected.</p>
<div id='status' role='status' aria-live='polite'></div>
<div class='card'><h2>Chord map</h2><div style='overflow:auto'><table><thead><tr><th>Time</th><th>Chord</th><th>Roman</th><th>Nashville</th><th>Action</th></tr></thead><tbody>{rows}</tbody></table></div></div>
<div class='card'><h2>Add chord</h2><form id='addChord' class='grid'><label>Symbol<input name='symbol' maxlength='{MAX_SYMBOL_CHARS}' required></label><label>Start seconds<input name='start_seconds' type='number' min='0' max='{MAX_CHORD_SECONDS}' step='0.01' required></label><label>End seconds<input name='end_seconds' type='number' min='0.01' max='{MAX_CHORD_SECONDS}' step='0.01' required></label><div><button type='submit'>Add chord</button></div></form></div>
<div class='card'><h2>Deterministic reharmonisation</h2><form id='reharmonize' class='grid'><label>Operation<select name='mode'><option value='simplify'>Simplify</option><option value='sophisticate'>Sophisticate</option></select></label><label>Style<select name='style'><option value='pop'>Pop</option><option value='jazz'>Jazz</option><option value='gospel'>Gospel</option><option value='cinematic'>Cinematic</option></select></label><div><button type='submit'>Apply to chord map</button></div></form></div>
<script>
const base='/projects/{encoded}/song-dna/chords'; const status=document.getElementById('status');
async function send(url, method, body){{status.textContent='Applying…';const r=await fetch(url,{{method,headers:body?{{'Content-Type':'application/json'}}:undefined,body:body?JSON.stringify(body):undefined}});const data=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(data.detail||'Request failed');status.textContent=`Saved Song DNA version ${{data.song_dna_version||''}}. Audio rendered: ${{data.audio_rendered===true?'yes':'no'}}.`;location.reload();}}
document.getElementById('addChord').addEventListener('submit',e=>{{e.preventDefault();const f=new FormData(e.currentTarget);send(base,'POST',{{symbol:f.get('symbol'),start_seconds:Number(f.get('start_seconds')),end_seconds:Number(f.get('end_seconds'))}}).catch(err=>status.textContent=err.message)}});
document.querySelectorAll('.replaceChord').forEach(form=>form.addEventListener('submit',e=>{{e.preventDefault();const f=new FormData(form);send(base+'/'+encodeURIComponent(form.dataset.id),'PATCH',{{symbol:f.get('symbol')}}).catch(err=>status.textContent=err.message)}}));
document.querySelectorAll('.deleteChord').forEach(btn=>btn.addEventListener('click',()=>send(base+'/'+encodeURIComponent(btn.dataset.id),'DELETE').catch(err=>status.textContent=err.message)));
document.getElementById('reharmonize').addEventListener('submit',e=>{{e.preventDefault();const f=new FormData(e.currentTarget);send(base+'/reharmonize','POST',{{mode:f.get('mode'),style:f.get('style')}}).catch(err=>status.textContent=err.message)}});
</script></main></body></html>"""
    )


__all__ = [
    "ChordDescriptor", "ChordEvent", "MAX_CHORD_EVENTS", "describe_chord", "normalize_chord_symbol",
    "parse_chord_symbol", "parse_key", "reharmonize_progression", "reharmonize_symbol", "router",
    "validate_progression",
]
