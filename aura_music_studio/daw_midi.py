from __future__ import annotations

import shutil
from html import escape
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import mido
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .branding import PRODUCT_FULL_NAME
from .daw import find_clip, load_session, save_session
from .daw_mixer_ui import JAVASCRIPT as BASE_DAW_MIXER_UI
from .performance_inputs import get_input
from .plans import AUDIO_TO_MIDI_CONTROL, DEEP_REVISION_HISTORY
from .revisions import create_revision
from .session import Clip, StudioSession, Track
from .song_dna import SongDNAStore
from .tenant_storage import project_path

router = APIRouter(tags=["Pulsar MIDI Piano Roll"])

_TPB = 480
_MAX_NOTES = 10000
_MAX_CONTROLS = 10000


class MidiNote(BaseModel):
    pitch: int = Field(ge=0, le=127)
    start_beat: float = Field(ge=0.0, le=100000.0)
    duration_beats: float = Field(gt=0.0, le=10000.0)
    velocity: int = Field(default=96, ge=1, le=127)
    channel: int = Field(default=0, ge=0, le=15)


class MidiCC(BaseModel):
    beat: float = Field(ge=0.0, le=100000.0)
    control: int = Field(ge=0, le=127)
    value: int = Field(ge=0, le=127)
    channel: int = Field(default=0, ge=0, le=15)


class MidiPitchBend(BaseModel):
    beat: float = Field(ge=0.0, le=100000.0)
    value: int = Field(ge=-8192, le=8191)
    channel: int = Field(default=0, ge=0, le=15)


class MidiDocument(BaseModel):
    notes: list[MidiNote] = Field(default_factory=list, max_length=_MAX_NOTES)
    cc: list[MidiCC] = Field(default_factory=list, max_length=_MAX_CONTROLS)
    pitch_bend: list[MidiPitchBend] = Field(default_factory=list, max_length=_MAX_CONTROLS)
    name: str = Field(default="Pulsar MIDI", min_length=1, max_length=160)


class CreateMidiClipRequest(BaseModel):
    name: str = Field(default="Pulsar MIDI", min_length=1, max_length=160)
    track_id: str | None = Field(default=None, max_length=80)
    channel: int = Field(default=0, ge=0, le=15)


class MidiTransformRequest(BaseModel):
    transpose_semitones: int = Field(default=0, ge=-48, le=48)
    velocity_delta: int = Field(default=0, ge=-126, le=126)
    quantize_grid_beats: float | None = Field(default=None, gt=0.0, le=16.0)
    quantize_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    quantize_duration: bool = False


class MidiGuideRequest(BaseModel):
    intent: str = Field(default="", max_length=1200)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(AUDIO_TO_MIDI_CONTROL):
        raise HTTPException(403, "Editable MIDI and piano roll tools require the Pro tier")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _session(project: Path) -> StudioSession:
    try:
        return load_session(project)
    except FileNotFoundError as exc:
        raise HTTPException(404, "This project does not have a DAW session yet") from exc


def _snapshot(project: Path, member, label: str) -> None:
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 40
    try:
        create_revision(project, label=label, reason="midi_edit", actor="Studio member", keep=keep)
    except Exception:
        pass


def _safe_midi_path(project: Path, value: str | Path, *, must_exist: bool = True) -> Path:
    raw = Path(value)
    root = project.resolve()
    target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if target != root and root not in target.parents:
        raise ValueError("MIDI source escapes the member project")
    if target.suffix.lower() not in {".mid", ".midi"}:
        raise ValueError("MIDI source must use .mid or .midi")
    if must_exist and not target.is_file():
        raise FileNotFoundError(target)
    return target


def _relative(project: Path, value: Path) -> str:
    return value.resolve().relative_to(project.resolve()).as_posix()


def _clip(session: StudioSession, clip_id: str) -> tuple[Track, Clip]:
    try:
        track, clip = find_clip(session, clip_id)
    except KeyError as exc:
        raise HTTPException(404, "MIDI clip not found") from exc
    if clip.kind != "midi" or not clip.source:
        raise HTTPException(400, "Clip is not editable MIDI")
    return track, clip


def _beat(value_ticks: int, ticks_per_beat: int) -> float:
    return round(float(value_ticks) / float(ticks_per_beat or _TPB), 6)


def read_midi_document(path: Path) -> MidiDocument:
    midi = mido.MidiFile(path)
    tpb = int(midi.ticks_per_beat or _TPB)
    absolute = 0
    active: dict[tuple[int, int], list[tuple[int, int]]] = {}
    notes: list[MidiNote] = []
    controls: list[MidiCC] = []
    bends: list[MidiPitchBend] = []
    name = path.stem
    for message in mido.merge_tracks(midi.tracks):
        absolute += int(message.time or 0)
        if message.type == "track_name" and getattr(message, "name", ""):
            name = str(message.name)[:160]
        elif message.type == "note_on" and int(message.velocity) > 0:
            active.setdefault((int(message.channel), int(message.note)), []).append((absolute, int(message.velocity)))
        elif message.type in {"note_off", "note_on"}:
            key = (int(message.channel), int(message.note))
            stack = active.get(key) or []
            if stack:
                start, velocity = stack.pop(0)
                if not stack:
                    active.pop(key, None)
                notes.append(MidiNote(
                    pitch=key[1],
                    start_beat=_beat(start, tpb),
                    duration_beats=_beat(max(1, absolute - start), tpb),
                    velocity=velocity,
                    channel=key[0],
                ))
        elif message.type == "control_change":
            controls.append(MidiCC(
                beat=_beat(absolute, tpb),
                control=int(message.control),
                value=int(message.value),
                channel=int(message.channel),
            ))
        elif message.type == "pitchwheel":
            bends.append(MidiPitchBend(
                beat=_beat(absolute, tpb),
                value=int(message.pitch),
                channel=int(message.channel),
            ))
    final_tick = max(absolute, tpb // 4)
    for (channel, pitch), stack in active.items():
        for start, velocity in stack:
            notes.append(MidiNote(
                pitch=pitch,
                start_beat=_beat(start, tpb),
                duration_beats=_beat(max(1, final_tick - start), tpb),
                velocity=velocity,
                channel=channel,
            ))
    notes.sort(key=lambda row: (row.start_beat, row.pitch, row.channel))
    controls.sort(key=lambda row: (row.beat, row.control, row.channel))
    bends.sort(key=lambda row: (row.beat, row.channel))
    return MidiDocument(notes=notes, cc=controls, pitch_bend=bends, name=name or "Pulsar MIDI")


def write_midi_document(path: Path, document: MidiDocument, *, bpm: float = 120.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(ticks_per_beat=_TPB)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=document.name[:160], time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(max(20.0, min(400.0, float(bpm or 120.0)))), time=0))
    events: list[tuple[int, int, mido.Message]] = []
    for note in document.notes:
        start = max(0, round(float(note.start_beat) * _TPB))
        end = max(start + 1, round((float(note.start_beat) + float(note.duration_beats)) * _TPB))
        events.append((start, 3, mido.Message("note_on", channel=note.channel, note=note.pitch, velocity=note.velocity, time=0)))
        events.append((end, 0, mido.Message("note_off", channel=note.channel, note=note.pitch, velocity=0, time=0)))
    for point in document.cc:
        tick = max(0, round(float(point.beat) * _TPB))
        events.append((tick, 1, mido.Message("control_change", channel=point.channel, control=point.control, value=point.value, time=0)))
    for point in document.pitch_bend:
        tick = max(0, round(float(point.beat) * _TPB))
        events.append((tick, 2, mido.Message("pitchwheel", channel=point.channel, pitch=point.value, time=0)))
    events.sort(key=lambda row: (row[0], row[1]))
    last = 0
    for tick, _priority, message in events:
        message.time = tick - last
        track.append(message)
        last = tick
    midi.save(path)
    return path


def transform_midi_document(document: MidiDocument, body: MidiTransformRequest) -> MidiDocument:
    transformed = document.model_copy(deep=True)
    grid = float(body.quantize_grid_beats or 0.0)
    strength = float(body.quantize_strength)
    for note in transformed.notes:
        note.pitch = max(0, min(127, int(note.pitch) + int(body.transpose_semitones)))
        note.velocity = max(1, min(127, int(note.velocity) + int(body.velocity_delta)))
        if grid > 0:
            target = round(note.start_beat / grid) * grid
            note.start_beat = max(0.0, round(note.start_beat + (target - note.start_beat) * strength, 6))
            if body.quantize_duration:
                duration_target = max(grid, round(note.duration_beats / grid) * grid)
                note.duration_beats = max(
                    0.001,
                    round(note.duration_beats + (duration_target - note.duration_beats) * strength, 6),
                )
    transformed.notes.sort(key=lambda row: (row.start_beat, row.pitch, row.channel))
    return transformed


def _summary(document: MidiDocument) -> dict:
    if document.notes:
        low = min(note.pitch for note in document.notes)
        high = max(note.pitch for note in document.notes)
        end = max(note.start_beat + note.duration_beats for note in document.notes)
        average_velocity = round(sum(note.velocity for note in document.notes) / len(document.notes), 2)
    else:
        low = high = None
        end = 0.0
        average_velocity = None
    return {
        "note_count": len(document.notes),
        "cc_count": len(document.cc),
        "pitch_bend_count": len(document.pitch_bend),
        "pitch_low": low,
        "pitch_high": high,
        "duration_beats": round(end, 4),
        "average_velocity": average_velocity,
        "symbolic_guide_only": True,
    }


def _public_clip(project_name: str, track: Track, clip: Clip, document: MidiDocument) -> dict:
    return {
        "clip_id": clip.id,
        "track_id": track.id,
        "track_name": track.name,
        "name": clip.name,
        "start": clip.start,
        "duration": clip.duration,
        "take_lane": clip.take_lane,
        "summary": _summary(document),
        "editor_url": f"/midi-editor?project={quote(project_name, safe='')}&clip={quote(clip.id, safe='')}",
        "source_path_exposed": False,
    }


def _create_clip(
    project: Path,
    session: StudioSession,
    *,
    name: str,
    track_id: str | None,
    source: Path,
) -> tuple[Track, Clip]:
    if track_id:
        try:
            track = session.find_track(track_id)
        except KeyError as exc:
            raise ValueError("MIDI track not found") from exc
        if track.role != "midi":
            raise ValueError("MIDI clips can only be added to MIDI tracks")
    else:
        track = session.add_track(name[:100], "midi")
    clip = Clip(
        id=uuid4().hex,
        name=name[:160],
        kind="midi",
        source=_relative(project, source),
        start=0.0,
        duration=0.0,
        metadata={"midi_editor": True, "symbolic_guide_only": True},
    )
    track.clips.append(clip)
    session.touch()
    return track, clip


def _save_document(project: Path, session: StudioSession, clip: Clip, document: MidiDocument) -> MidiDocument:
    source = _safe_midi_path(project, str(clip.source or ""), must_exist=False)
    write_midi_document(source, document, bpm=session.bpm)
    summary = _summary(document)
    clip.duration = float(summary["duration_beats"]) * 60.0 / max(1.0, float(session.bpm or 120.0))
    clip.metadata = {**clip.metadata, **summary, "midi_editor": True, "symbolic_guide_only": True}
    session.touch()
    return document


@router.get("/projects/{project_name}/daw/midi")
def list_midi_clips(project_name: str, request: Request):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    rows = []
    for track in session.tracks:
        for clip in track.clips:
            if clip.kind != "midi" or not clip.source:
                continue
            try:
                document = read_midi_document(_safe_midi_path(project, clip.source))
            except Exception:
                continue
            rows.append(_public_clip(project_name, track, clip, document))
    return {"clips": rows, "bpm": session.bpm, "symbolic_guide_only": True}


@router.post("/projects/{project_name}/daw/midi")
def create_midi_clip(project_name: str, body: CreateMidiClipRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    _snapshot(project, member, "Before creating MIDI clip")
    source = project / "input" / "midi" / f"{uuid4().hex}.mid"
    document = MidiDocument(name=body.name)
    write_midi_document(source, document, bpm=session.bpm)
    try:
        track, clip = _create_clip(project, session, name=body.name, track_id=body.track_id, source=source)
    except ValueError as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    clip.metadata["default_channel"] = body.channel
    save_session(project, session)
    return {"clip": _public_clip(project_name, track, clip, document), "document": document.model_dump(mode="json")}


@router.post("/projects/{project_name}/daw/midi/import-performance/{input_id}")
def import_performance_midi(project_name: str, input_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    try:
        item = get_input(project, input_id)
    except KeyError as exc:
        raise HTTPException(404, "Performance guide not found") from exc
    if not item.midi_ref:
        raise HTTPException(409, "This performance guide does not have MIDI transcription")
    try:
        source = _safe_midi_path(project, item.midi_ref)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "Performance MIDI is unavailable") from exc
    _snapshot(project, member, "Before importing performance MIDI")
    destination = project / "input" / "midi" / f"performance-{uuid4().hex}.mid"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    document = read_midi_document(destination)
    track, clip = _create_clip(project, session, name=item.label or "Performance MIDI", track_id=None, source=destination)
    _save_document(project, session, clip, document)
    clip.metadata["performance_input_id"] = item.id
    save_session(project, session)
    return {"clip": _public_clip(project_name, track, clip, document), "document": document.model_dump(mode="json")}


@router.get("/projects/{project_name}/daw/midi/{clip_id}")
def get_midi_clip(project_name: str, clip_id: str, request: Request):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    track, clip = _clip(session, clip_id)
    try:
        document = read_midi_document(_safe_midi_path(project, str(clip.source or "")))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "MIDI file is unavailable") from exc
    return {
        "clip": _public_clip(project_name, track, clip, document),
        "document": document.model_dump(mode="json"),
        "bpm": session.bpm,
        "key": session.key,
        "meter": session.meter,
    }


@router.put("/projects/{project_name}/daw/midi/{clip_id}")
def replace_midi_document(project_name: str, clip_id: str, body: MidiDocument, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    track, clip = _clip(session, clip_id)
    _snapshot(project, member, f"Before editing MIDI · {clip.name}")
    _save_document(project, session, clip, body)
    session.generation_history.append({
        "action": "midi_document_edit",
        "clip_id": clip.id,
        "track_id": track.id,
        **_summary(body),
    })
    save_session(project, session)
    return {"clip": _public_clip(project_name, track, clip, body), "document": body.model_dump(mode="json")}


@router.post("/projects/{project_name}/daw/midi/{clip_id}/transform")
def transform_midi(project_name: str, clip_id: str, body: MidiTransformRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    track, clip = _clip(session, clip_id)
    try:
        source = _safe_midi_path(project, str(clip.source or ""))
        document = read_midi_document(source)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "MIDI file is unavailable") from exc
    _snapshot(project, member, f"Before transforming MIDI · {clip.name}")
    transformed = transform_midi_document(document, body)
    _save_document(project, session, clip, transformed)
    session.generation_history.append({
        "action": "midi_transform",
        "clip_id": clip.id,
        "track_id": track.id,
        "transpose_semitones": body.transpose_semitones,
        "velocity_delta": body.velocity_delta,
        "quantize_grid_beats": body.quantize_grid_beats,
        "quantize_strength": body.quantize_strength,
        "quantize_duration": body.quantize_duration,
    })
    save_session(project, session)
    return {"clip": _public_clip(project_name, track, clip, transformed), "document": transformed.model_dump(mode="json")}


@router.post("/projects/{project_name}/daw/midi/{clip_id}/use-as-guide")
def use_midi_as_generation_guide(project_name: str, clip_id: str, body: MidiGuideRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    track, clip = _clip(session, clip_id)
    try:
        source = _safe_midi_path(project, str(clip.source or ""))
        document = read_midi_document(source)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, "MIDI file is unavailable") from exc
    _snapshot(project, member, f"Before applying MIDI guide · {clip.name}")
    summary = _summary(document)
    context = (
        f"Use MIDI guide {clip.name!r} as an editable musical control layer. Preserve its note contour, rhythm, note lengths, "
        f"velocity dynamics and controller expression unless the creator explicitly asks for changes. {body.intent.strip()}"
    ).strip()
    guides = list(session.project_dna.get("midi_guides") or [])
    guides = [row for row in guides if row.get("clip_id") != clip.id]
    guides.append({
        "clip_id": clip.id,
        "track_id": track.id,
        "name": clip.name,
        "midi_ref": _relative(project, source),
        "generation_context": context,
        **summary,
    })
    session.project_dna["midi_guides"] = guides[-30:]
    clip.metadata["generation_guide"] = True
    clip.metadata["generation_context"] = context
    session.generation_history.append({"action": "use_midi_as_generation_guide", "clip_id": clip.id, "track_id": track.id})
    save_session(project, session)

    dna_store = SongDNAStore(project)
    if dna_store.path.is_file():
        dna = dna_store.load()
        dna_guides = list(dna.metadata.get("midi_guides") or [])
        dna_guides = [row for row in dna_guides if row.get("clip_id") != clip.id]
        dna_guides.append(guides[-1])
        dna.metadata["midi_guides"] = dna_guides[-30:]
        dna_store.save(dna)
    return {
        "applied": True,
        "clip_id": clip.id,
        "generation_context": context,
        "summary": summary,
        "symbolic_guide_only": True,
        "audio_rendered": False,
    }


MIDI_DAW_NAV = r"""
(() => {
  function href() {
    const project = typeof currentProject !== 'undefined' && currentProject ? currentProject : '';
    return '/midi-editor' + (project ? '?project=' + encodeURIComponent(project) : '');
  }
  function sync() {
    const button = document.getElementById('pulsarMidiNav');
    if (button) button.href = href();
  }
  function install() {
    if (!(typeof FLAGS !== 'undefined' && FLAGS.multitrack)) return;
    if (document.getElementById('pulsarMidiNav')) return sync();
    const toolbar = document.querySelector('.toolbar');
    if (!toolbar) return;
    const link = document.createElement('a');
    link.id = 'pulsarMidiNav';
    link.className = 'btn';
    link.textContent = '🎹 MIDI Piano Roll';
    link.href = href();
    link.title = 'Edit real MIDI notes, velocity, timing, controller data and Aura generation guides';
    toolbar.appendChild(link);
    document.getElementById('projectSelect')?.addEventListener('change', () => setTimeout(sync, 0));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true});
  else install();
})();
"""


@router.get("/daw/mixer-ui.js", include_in_schema=False)
def daw_mixer_with_midi_navigation():
    """Extend the existing DAW UI script without replacing mixer/automation behavior."""
    return Response(
        content=BASE_DAW_MIXER_UI + "\n" + MIDI_DAW_NAV,
        media_type="application/javascript",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/midi-editor", response_class=HTMLResponse, include_in_schema=False)
def midi_editor(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse("/signin?next=/midi-editor", status_code=303)
    if not member.plan.has(AUDIO_TO_MIDI_CONTROL):
        return RedirectResponse("/pricing", status_code=303)
    html = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>MIDI Piano Roll — __BRAND__</title><style>
:root{--bg:#05040a;--panel:#100b18;--line:#ffffff20;--gold:#efc86c;--violet:#9b6cff;--cyan:#59dff8;--green:#76dfa6;--muted:#bcb4c7;--red:#ff8fa4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#42175d66,transparent 29%),#05040a;color:#fff;font-family:Inter,system-ui,sans-serif}button,input,select,textarea{font:inherit}.wrap{width:min(1500px,calc(100% - 20px));margin:auto;padding:18px 0 80px}.top,.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.top{justify-content:space-between}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase}.btn,button{border:1px solid var(--line);background:#ffffff08;color:#fff;border-radius:10px;padding:8px 10px;font-weight:850;cursor:pointer;text-decoration:none}.primary{border:0;background:linear-gradient(115deg,var(--gold),var(--violet));color:#160c1c}.good{border-color:#76dfa655;color:var(--green)}.danger{border-color:#ff8fa455;color:#ffd7de}select,input,textarea{border:1px solid var(--line);background:#08060d;color:#fff;border-radius:9px;padding:8px}.panel{border:1px solid var(--line);background:#0c0913eb;border-radius:16px;padding:12px;margin-top:10px}.toolbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);font-size:.78rem;line-height:1.5}.editor{display:grid;grid-template-columns:1fr 310px;gap:10px}.roll-shell{overflow:auto;height:620px;border:1px solid var(--line);border-radius:12px;background:#06050a}.roll{position:relative;width:4000px;height:1536px;background-image:linear-gradient(to right,#ffffff12 1px,transparent 1px),linear-gradient(to bottom,#ffffff0d 1px,transparent 1px);background-size:50px 24px}.roll:after{content:'';position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(to right,transparent 0,transparent 199px,#efc86c22 200px)}.note{position:absolute;height:20px;border:1px solid #e9c96c99;border-radius:5px;background:linear-gradient(90deg,#9b6cff,#59dff8);overflow:hidden;cursor:pointer;min-width:5px}.note.sel{outline:2px solid #fff}.inspector{display:grid;gap:8px}.field label{display:block;color:var(--muted);font-size:.7rem;margin-bottom:3px}.field input,.field textarea{width:100%}.status{white-space:pre-wrap;border:1px solid var(--line);border-radius:10px;padding:9px;color:var(--muted);margin-top:8px}.pill{border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem;color:var(--gold)}@media(max-width:900px){.editor{grid-template-columns:1fr}.roll-shell{height:500px}}
</style></head><body><main class='wrap'><div class='top'><a class='brand' href='/daw'>__BRAND__<small>Pro MIDI Piano Roll</small></a><div><a class='btn' href='/daw'>Visual DAW</a> <a class='btn' href='/dashboard'>Dashboard</a></div></div><section class='panel'><div class='toolbar'><select id='project'></select><button onclick='loadProject()'>Open Project</button><select id='clip'></select><button onclick='loadClip()'>Open MIDI</button><button onclick='createClip()'>+ New MIDI Clip</button><button class='good' onclick='useGuide()'>Use as Aura Guide</button><span id='summary' class='pill'>No MIDI loaded</span></div><p class='muted'>Click the piano roll to add notes. Select a note to edit pitch, timing, length and velocity. MIDI is a symbolic control layer; it becomes final audio only when a connected real music renderer performs it.</p></section><section class='panel toolbar'><label>Grid <select id='grid'><option value='1'>1/4</option><option value='.5'>1/8</option><option value='.25' selected>1/16</option><option value='.125'>1/32</option></select></label><button onclick='quantize()'>Quantize</button><button onclick='transpose(-12)'>-12</button><button onclick='transpose(-1)'>-1</button><button onclick='transpose(1)'>+1</button><button onclick='transpose(12)'>+12</button><button class='primary' onclick='save()'>Save MIDI</button></section><section class='editor'><div class='roll-shell'><div id='roll' class='roll'></div></div><aside class='panel inspector'><h3>Selected Note</h3><div id='noNote' class='muted'>Select or add a note.</div><div id='noteFields' style='display:none'><div class='field'><label>Pitch (0–127)</label><input id='pitch' type='number' min='0' max='127'></div><div class='field'><label>Start beat</label><input id='start' type='number' min='0' step='.125'></div><div class='field'><label>Duration beats</label><input id='duration' type='number' min='.01' step='.125'></div><div class='field'><label>Velocity</label><input id='velocity' type='number' min='1' max='127'></div><button onclick='applyNote()'>Apply Note</button> <button class='danger' onclick='deleteNote()'>Delete</button></div><h3>Controller / Pitch Bend</h3><div class='muted'>Controller and pitch-bend events are stored in the actual MIDI file. The current editor exposes validated JSON points while graphical controller lanes are built next.</div><div class='field'><label>CC points</label><textarea id='cc' rows='5'>[]</textarea></div><div class='field'><label>Pitch bend</label><textarea id='bend' rows='5'>[]</textarea></div><div class='field'><label>Aura generation intent</label><textarea id='intent' rows='4' placeholder='Example: use this chord rhythm as the piano guide but make the performance more human and expressive.'></textarea></div><div id='status' class='status'>MIDI editor ready.</div></aside></section></main><script>
let projects=[],doc={notes:[],cc:[],pitch_bend:[],name:'Pulsar MIDI'},selected=-1,currentProject='',currentClip='';const $=id=>document.getElementById(id),PX=50,ROW=24,MAXP=95,MINP=32;async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch{}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function status(v,bad=false){$('status').textContent=v;$('status').style.color=bad?'var(--red)':'var(--muted)'}function enc(v){return encodeURIComponent(v)}function snap(v){const g=Number($('grid').value)||.25;return Math.max(0,Math.round(v/g)*g)}async function boot(){try{projects=await api('/projects');$('project').innerHTML=projects.map(p=>`<option>${p.name}</option>`).join('');const q=new URLSearchParams(location.search);if(q.get('project'))$('project').value=q.get('project');await loadProject();if(q.get('clip')){$('clip').value=q.get('clip');await loadClip()}}catch(e){status(e.message,true)}}async function loadProject(){currentProject=$('project').value;if(!currentProject)return;try{const d=await api(`/projects/${enc(currentProject)}/daw/midi`);$('clip').innerHTML=(d.clips||[]).map(c=>`<option value="${c.clip_id}">${c.name} · ${c.summary.note_count} notes</option>`).join('');if(d.clips?.length){currentClip=$('clip').value;await loadClip()}else{doc={notes:[],cc:[],pitch_bend:[],name:'Pulsar MIDI'};currentClip='';render()}}catch(e){status(e.message,true)}}async function createClip(){if(!currentProject)return;const name=prompt('MIDI clip name','Pulsar MIDI')||'Pulsar MIDI';try{const d=await api(`/projects/${enc(currentProject)}/daw/midi`,{method:'POST',body:JSON.stringify({name})});await loadProject();$('clip').value=d.clip.clip_id;await loadClip();status('MIDI clip created.')}catch(e){status(e.message,true)}}async function loadClip(){currentClip=$('clip').value;if(!currentClip)return;try{const d=await api(`/projects/${enc(currentProject)}/daw/midi/${enc(currentClip)}`);doc=d.document;selected=-1;$('cc').value=JSON.stringify(doc.cc||[],null,2);$('bend').value=JSON.stringify(doc.pitch_bend||[],null,2);render();status(`Loaded ${doc.notes.length} notes at ${d.bpm} BPM.`)}catch(e){status(e.message,true)}}function render(){const roll=$('roll');roll.innerHTML='';for(let p=MAXP;p>=MINP;p--){const l=document.createElement('div');l.style.cssText=`position:absolute;left:0;top:${(MAXP-p)*ROW}px;width:42px;height:${ROW}px;border-right:1px solid #ffffff22;color:${p%12===0?'var(--gold)':'#aaa'};font-size:10px;padding:5px`;l.textContent=p;roll.appendChild(l)}(doc.notes||[]).forEach((n,i)=>{if(n.pitch<MINP||n.pitch>MAXP)return;const b=document.createElement('div');b.className='note'+(selected===i?' sel':'');b.style.left=(44+n.start_beat*PX)+'px';b.style.width=Math.max(6,n.duration_beats*PX)+'px';b.style.top=((MAXP-n.pitch)*ROW+2)+'px';b.title=`Pitch ${n.pitch} · beat ${n.start_beat} · vel ${n.velocity}`;b.onclick=e=>{e.stopPropagation();selectNote(i)};roll.appendChild(b)});$('summary').textContent=`${doc.notes?.length||0} notes · ${doc.cc?.length||0} CC · ${doc.pitch_bend?.length||0} bend`;refreshInspector()}$('roll').addEventListener('click',e=>{const r=$('roll').getBoundingClientRect(),x=e.clientX-r.left-44,y=e.clientY-r.top;const beat=snap(Math.max(0,x/PX)),pitch=Math.max(MINP,Math.min(MAXP,MAXP-Math.floor(y/ROW)));doc.notes.push({pitch,start_beat:beat,duration_beats:Number($('grid').value)||.25,velocity:96,channel:0});doc.notes.sort((a,b)=>a.start_beat-b.start_beat||a.pitch-b.pitch);selected=doc.notes.findIndex(n=>n.pitch===pitch&&n.start_beat===beat);render()});function selectNote(i){selected=i;render()}function refreshInspector(){const n=doc.notes?.[selected];$('noNote').style.display=n?'none':'block';$('noteFields').style.display=n?'block':'none';if(!n)return;$('pitch').value=n.pitch;$('start').value=n.start_beat;$('duration').value=n.duration_beats;$('velocity').value=n.velocity}function applyNote(){const n=doc.notes[selected];if(!n)return;n.pitch=Math.max(0,Math.min(127,Number($('pitch').value)));n.start_beat=Math.max(0,Number($('start').value));n.duration_beats=Math.max(.01,Number($('duration').value));n.velocity=Math.max(1,Math.min(127,Number($('velocity').value)));render()}function deleteNote(){if(selected<0)return;doc.notes.splice(selected,1);selected=-1;render()}async function save(){if(!currentClip)return status('Create or open a MIDI clip first.',true);try{doc.cc=JSON.parse($('cc').value||'[]');doc.pitch_bend=JSON.parse($('bend').value||'[]');const d=await api(`/projects/${enc(currentProject)}/daw/midi/${enc(currentClip)}`,{method:'PUT',body:JSON.stringify(doc)});doc=d.document;render();status('MIDI saved with revision history.')}catch(e){status(e.message,true)}}async function transform(body){if(!currentClip)return;try{const d=await api(`/projects/${enc(currentProject)}/daw/midi/${enc(currentClip)}/transform`,{method:'POST',body:JSON.stringify(body)});doc=d.document;$('cc').value=JSON.stringify(doc.cc||[],null,2);$('bend').value=JSON.stringify(doc.pitch_bend||[],null,2);render();status('MIDI transformed.')}catch(e){status(e.message,true)}}function quantize(){transform({quantize_grid_beats:Number($('grid').value),quantize_strength:1,quantize_duration:false})}function transpose(n){transform({transpose_semitones:n})}async function useGuide(){if(!currentClip)return status('Open a MIDI clip first.',true);try{const d=await api(`/projects/${enc(currentProject)}/daw/midi/${enc(currentClip)}/use-as-guide`,{method:'POST',body:JSON.stringify({intent:$('intent').value})});status(`Aura guide active · ${d.summary.note_count} notes · symbolic until rendered by a real music engine.`)}catch(e){status(e.message,true)}}boot();
</script></body></html>"""
    return HTMLResponse(html.replace("__BRAND__", escape(PRODUCT_FULL_NAME)))


__all__ = [
    "router",
    "MidiDocument",
    "MidiNote",
    "MidiCC",
    "MidiPitchBend",
    "MidiTransformRequest",
    "read_midi_document",
    "write_midi_document",
    "transform_midi_document",
    "MIDI_DAW_NAV",
]
