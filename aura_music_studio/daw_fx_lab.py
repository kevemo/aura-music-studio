from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .daw import load_session, save_session
from .daw_api import _project, _snapshot
from .effects import compile_ffmpeg_chain
from .plans import ADVANCED_FX, STANDARD_FX
from .session import Effect

router = APIRouter(tags=["Instrument and FX Lab"])

STANDARD_TYPES = {
    "gain", "eq", "highpass", "lowpass", "compressor", "limiter", "gate", "deesser",
    "reverb", "delay", "saturation", "chorus", "stereo_width",
}
ADVANCED_TYPES = {"distortion", "exciter", "flanger", "phaser", "tremolo", "pitch_shift", "doubler"}

DEFAULTS = {
    "gain": {"db": 0.0},
    "highpass": {"hz": 80.0},
    "lowpass": {"hz": 18000.0},
    "eq": {"low_db": 0.0, "low_hz": 120.0, "mid_db": 0.0, "mid_hz": 1800.0, "high_db": 0.0, "high_hz": 8500.0},
    "compressor": {"threshold_db": -18.0, "ratio": 2.5, "attack_ms": 15.0, "release_ms": 160.0},
    "limiter": {"ceiling_db": -1.0, "attack_ms": 5.0, "release_ms": 50.0},
    "gate": {"threshold_db": -45.0, "ratio": 8.0, "attack_ms": 10.0, "release_ms": 120.0},
    "deesser": {"frequency_hz": 6500.0, "reduction_db": 4.0},
    "reverb": {"predelay_ms": 30.0, "mix": 0.18},
    "delay": {"delay_ms": 240.0, "feedback": 0.25},
    "saturation": {"drive": 1.35},
    "chorus": {"delay_ms": 18.0, "decay": 0.35, "rate_hz": 0.8, "depth": 2.0},
    "stereo_width": {"width": 1.0},
    "distortion": {"drive": 2.0},
    "exciter": {"amount": 2.0, "frequency_hz": 7000.0},
    "flanger": {"delay_ms": 2.0, "depth_ms": 2.0, "feedback": 0.0, "rate_hz": 0.5},
    "phaser": {"rate_hz": 0.5, "decay": 0.4},
    "tremolo": {"rate_hz": 5.0, "depth": 0.5},
    "pitch_shift": {"semitones": 0.0},
    "doubler": {"delay_ms": 22.0, "mix": 0.18, "width": 1.25},
}

BOUNDS = {
    "gain": {"db": (-24, 24)},
    "highpass": {"hz": (20, 1000)},
    "lowpass": {"hz": (1000, 22000)},
    "eq": {"low_db": (-18, 18), "low_hz": (40, 500), "mid_db": (-18, 18), "mid_hz": (150, 12000), "high_db": (-18, 18), "high_hz": (2000, 20000)},
    "compressor": {"threshold_db": (-60, 0), "ratio": (1, 20), "attack_ms": (0.1, 500), "release_ms": (5, 3000)},
    "limiter": {"ceiling_db": (-12, 0), "attack_ms": (0.1, 100), "release_ms": (5, 1000)},
    "gate": {"threshold_db": (-80, 0), "ratio": (1, 20), "attack_ms": (0.1, 500), "release_ms": (5, 3000)},
    "deesser": {"frequency_hz": (3500, 11000), "reduction_db": (0, 12)},
    "reverb": {"predelay_ms": (1, 500), "mix": (0.01, 0.8)},
    "delay": {"delay_ms": (1, 2000), "feedback": (0, 0.9)},
    "saturation": {"drive": (1, 12)},
    "chorus": {"delay_ms": (5, 40), "decay": (0.05, 0.9), "rate_hz": (0.1, 5), "depth": (0.1, 10)},
    "stereo_width": {"width": (0, 2)},
    "distortion": {"drive": (1, 12)},
    "exciter": {"amount": (0, 8), "frequency_hz": (2000, 18000)},
    "flanger": {"delay_ms": (0, 30), "depth_ms": (0, 10), "feedback": (-95, 95), "rate_hz": (0.1, 10)},
    "phaser": {"rate_hz": (0.1, 2), "decay": (0, 0.99)},
    "tremolo": {"rate_hz": (0.1, 20), "depth": (0, 1)},
    "pitch_shift": {"semitones": (-12, 12)},
    "doubler": {"delay_ms": (8, 45), "mix": (0.02, 0.5), "width": (1, 2)},
}

PRESETS = {
    "vocal_polish": {
        "title": "Vocal Polish",
        "roles": {"vocals", "backing_vocals"},
        "effects": [
            ("highpass", {"hz": 80}),
            ("deesser", {"frequency_hz": 6500, "reduction_db": 4}),
            ("compressor", {"threshold_db": -20, "ratio": 2.5, "attack_ms": 18, "release_ms": 150}),
            ("eq", {"mid_db": 1.5, "mid_hz": 2800, "high_db": 1, "high_hz": 9000}),
        ],
    },
    "warm_vocal": {
        "title": "Warm Vocal",
        "roles": {"vocals", "backing_vocals"},
        "effects": [
            ("highpass", {"hz": 70}),
            ("saturation", {"drive": 1.25}),
            ("compressor", {"threshold_db": -22, "ratio": 2, "attack_ms": 25, "release_ms": 190}),
            ("reverb", {"predelay_ms": 34, "mix": 0.12}),
        ],
    },
    "punch_drums": {
        "title": "Punch Drums",
        "roles": {"drums", "percussion"},
        "effects": [
            ("highpass", {"hz": 35}),
            ("compressor", {"threshold_db": -16, "ratio": 3.5, "attack_ms": 22, "release_ms": 110}),
            ("saturation", {"drive": 1.45}),
        ],
    },
    "bass_control": {
        "title": "Bass Control",
        "roles": {"bass"},
        "effects": [
            ("highpass", {"hz": 28}),
            ("compressor", {"threshold_db": -20, "ratio": 3, "attack_ms": 30, "release_ms": 180}),
            ("lowpass", {"hz": 11000}),
        ],
    },
    "master_clarity": {
        "title": "Master Clarity",
        "roles": {"master"},
        "effects": [
            ("eq", {"high_db": 0.75, "high_hz": 9000}),
            ("compressor", {"threshold_db": -12, "ratio": 1.5, "attack_ms": 35, "release_ms": 240}),
            ("limiter", {"ceiling_db": -1, "attack_ms": 5, "release_ms": 60}),
        ],
    },
}


def normalize_parameters(effect_type: str, parameters: dict | None = None) -> dict[str, float]:
    if effect_type not in DEFAULTS:
        raise ValueError("Unsupported FX type")
    result = deepcopy(DEFAULTS[effect_type])
    for key, value in dict(parameters or {}).items():
        if key not in BOUNDS.get(effect_type, {}):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{effect_type}.{key} must be numeric") from exc
        low, high = BOUNDS[effect_type][key]
        result[key] = max(low, min(high, number))
    return result


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(STANDARD_FX):
        raise HTTPException(403, "Editable Instrument & FX Lab unlocks on the Basic tier")
    return member


def _allowed(member) -> set[str]:
    return STANDARD_TYPES | (ADVANCED_TYPES if member.plan.has(ADVANCED_FX) else set())


def _session_or_404(project):
    try:
        return load_session(project)
    except FileNotFoundError as exc:
        raise HTTPException(404, "This project does not have a DAW session yet") from exc


def _track(session, track_id: str):
    try:
        return session.find_track(track_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc


def _payload(track) -> dict:
    return {
        "track_id": track.id,
        "track_name": track.name,
        "role": track.role,
        "effects": [fx.model_dump(mode="json") for fx in track.effects],
        "real_audio_filter_chain": compile_ffmpeg_chain(track.effects),
    }


class AddFx(BaseModel):
    type: str = Field(min_length=1, max_length=60)
    parameters: dict = Field(default_factory=dict)


class EditFx(BaseModel):
    enabled: bool | None = None
    parameters: dict | None = None


class FxOrder(BaseModel):
    effect_ids: list[str] = Field(min_length=1, max_length=64)


class ApplyPreset(BaseModel):
    preset_id: str = Field(min_length=1, max_length=80)
    replace_chain: bool = True


@router.get("/projects/{project_name}/daw/fx/catalog")
def fx_catalog(project_name: str, request: Request):
    member = _member(request)
    _project(project_name)
    allowed = _allowed(member)
    return {
        "plan": member.plan.id,
        "effect_types": [
            {
                "type": name,
                "defaults": DEFAULTS[name],
                "bounds": BOUNDS[name],
                "advanced": name in ADVANCED_TYPES,
            }
            for name in sorted(allowed)
        ],
        "presets": [
            {"id": key, "title": value["title"], "roles": sorted(value["roles"])}
            for key, value in PRESETS.items()
            if all(kind in allowed for kind, _ in value["effects"])
        ],
        "render_engine": "real_audio_ffmpeg",
    }


@router.get("/projects/{project_name}/daw/tracks/{track_id}/fx")
def get_fx(project_name: str, track_id: str, request: Request):
    _member(request)
    project = _project(project_name)
    return _payload(_track(_session_or_404(project), track_id))


@router.post("/projects/{project_name}/daw/tracks/{track_id}/fx")
def add_fx(project_name: str, track_id: str, body: AddFx, request: Request):
    member = _member(request)
    kind = body.type.strip().lower()
    if kind not in _allowed(member):
        raise HTTPException(403, "This effect is not available on the current tier")
    project = _project(project_name)
    session = _session_or_404(project)
    track = _track(session, track_id)
    try:
        params = normalize_parameters(kind, body.parameters)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _snapshot(project, member, f"Before adding {kind} FX")
    track.effects.append(Effect(type=kind, parameters=params))
    save_session(project, session)
    return _payload(track)


@router.patch("/projects/{project_name}/daw/tracks/{track_id}/fx/{effect_id}")
def edit_fx(project_name: str, track_id: str, effect_id: str, body: EditFx, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session_or_404(project)
    track = _track(session, track_id)
    effect = next((fx for fx in track.effects if fx.id == effect_id), None)
    if effect is None:
        raise HTTPException(404, "Effect not found")
    _snapshot(project, member, f"Before editing {effect.type} FX")
    if body.enabled is not None:
        effect.enabled = body.enabled
    if body.parameters is not None:
        try:
            effect.parameters = normalize_parameters(effect.type, body.parameters)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    save_session(project, session)
    return _payload(track)


@router.delete("/projects/{project_name}/daw/tracks/{track_id}/fx/{effect_id}")
def remove_fx(project_name: str, track_id: str, effect_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session_or_404(project)
    track = _track(session, track_id)
    if not any(fx.id == effect_id for fx in track.effects):
        raise HTTPException(404, "Effect not found")
    _snapshot(project, member, "Before removing DAW FX")
    track.effects = [fx for fx in track.effects if fx.id != effect_id]
    save_session(project, session)
    return _payload(track)


@router.put("/projects/{project_name}/daw/tracks/{track_id}/fx/order")
def reorder_fx(project_name: str, track_id: str, body: FxOrder, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session_or_404(project)
    track = _track(session, track_id)
    by_id = {fx.id: fx for fx in track.effects}
    if len(body.effect_ids) != len(by_id) or set(body.effect_ids) != set(by_id):
        raise HTTPException(400, "FX order must contain every effect exactly once")
    _snapshot(project, member, "Before reordering DAW FX")
    track.effects = [by_id[fx_id] for fx_id in body.effect_ids]
    save_session(project, session)
    return _payload(track)


@router.post("/projects/{project_name}/daw/tracks/{track_id}/fx/preset")
def apply_preset(project_name: str, track_id: str, body: ApplyPreset, request: Request):
    member = _member(request)
    preset = PRESETS.get(body.preset_id)
    if preset is None:
        raise HTTPException(404, "FX preset not found")
    project = _project(project_name)
    session = _session_or_404(project)
    track = _track(session, track_id)
    if track.role not in preset["roles"]:
        raise HTTPException(409, f"{preset['title']} is intended for {', '.join(sorted(preset['roles']))} tracks")
    if any(kind not in _allowed(member) for kind, _ in preset["effects"]):
        raise HTTPException(403, "This preset requires a higher tier")
    chain = [Effect(type=kind, parameters=normalize_parameters(kind, params)) for kind, params in preset["effects"]]
    _snapshot(project, member, f"Before applying {preset['title']}")
    track.effects = chain if body.replace_chain else track.effects + chain
    save_session(project, session)
    return _payload(track)


LAB_HTML = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Instrument & FX Lab</title><style>
:root{--bg:#07050d;--panel:#14101d;--line:#ffffff20;--gold:#efc86f;--violet:#9e70ff;--muted:#c3bfd2;--bad:#ff91a6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#40175a,transparent 31%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1120px,calc(100% - 28px));margin:auto;padding:35px 0 55px}.card{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:15px;margin:10px 0}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.field,.btn{padding:10px;border-radius:10px;border:1px solid var(--line);background:#09070f;color:#fff}.btn{cursor:pointer;font-weight:800;text-decoration:none}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.gold{color:var(--gold)}.muted{color:var(--muted);line-height:1.5}.fx{border:1px solid #ffffff1c;border-radius:12px;padding:12px;margin:8px 0}.fxhead{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.chain{font-family:ui-monospace,monospace;font-size:.75rem;word-break:break-all}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px;margin:10px 0}.notice.show{display:block}.params{font-family:ui-monospace,monospace;font-size:.76rem}.spacer{flex:1}</style></head><body><main class='wrap'>
<p class='gold'><b>PULSAR-FREQUENCY HOUSE · REAL-AUDIO PRODUCTION</b></p><h1>Instrument & FX Lab</h1><p class='muted'>Every enabled effect here is persisted to the project DAW session and compiled into the real waveform render chain. Edits create project revision snapshots before mutation.</p>
<div id='notice' class='notice'></div><div class='card row'><select id='track' class='field'></select><select id='effect' class='field'></select><button id='add' class='btn primary'>Add effect</button><select id='preset' class='field'></select><button id='apply' class='btn'>Apply chain preset</button><span class='spacer'></span><a id='songLink' class='btn'>Song DNA</a><a class='btn' href='/daw'>DAW</a></div>
<div class='card'><b>Real render chain</b><div id='chain' class='chain muted'>Loading…</div></div><div id='effects'></div>
</main><script>
const q=new URLSearchParams(location.search),project=q.get('project'),root=project?`/projects/${encodeURIComponent(project)}/daw`:'';const $=id=>document.getElementById(id);let catalog=null,current=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'var(--bad)':''}async function api(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
async function boot(){if(!project){document.body.innerHTML='<main class="wrap"><div class="card">Open Instrument & FX Lab from a Song DNA project.</div></main>';return}$('songLink').href='/song-editor/'+encodeURIComponent(project);const [s,c]=await Promise.all([api(root+'/session'),api(root+'/fx/catalog')]);catalog=c;$('track').innerHTML=(s.tracks||[]).map(t=>`<option value="${esc(t.id)}">${esc(t.name)} · ${esc(t.role)}</option>`).join('');$('effect').innerHTML=c.effect_types.map(x=>`<option value="${esc(x.type)}">${esc(x.type)}${x.advanced?' · Pro':''}</option>`).join('');$('preset').innerHTML='<option value="">Chain preset</option>'+c.presets.map(x=>`<option value="${esc(x.id)}">${esc(x.title)}</option>`).join('');await load()}
async function load(){current=await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx`);$('chain').textContent=current.real_audio_filter_chain||'No active effects.';$('effects').innerHTML=current.effects.map((f,i)=>`<div class="fx"><div class="fxhead"><div><b>${esc(f.type)}</b> · ${f.enabled?'enabled':'disabled'}<div class="params muted">${esc(JSON.stringify(f.parameters))}</div></div><div class="row"><button class="btn" onclick="moveFx(${i},-1)" ${i===0?'disabled':''}>↑</button><button class="btn" onclick="moveFx(${i},1)" ${i===current.effects.length-1?'disabled':''}>↓</button><button class="btn" onclick="editParams('${esc(f.id)}')">Edit</button><button class="btn" onclick="toggle('${esc(f.id)}',${!f.enabled})">${f.enabled?'Disable':'Enable'}</button><button class="btn" onclick="delFx('${esc(f.id)}')">Remove</button></div></div></div>`).join('')||'<div class="card muted">No effects yet.</div>'}
async function toggle(id,en){try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({enabled:en})});await load()}catch(e){note(e.message,true)}}
async function editParams(id){const fx=current.effects.find(x=>x.id===id);if(!fx)return;const raw=prompt(`Edit ${fx.type} parameters as JSON:`,JSON.stringify(fx.parameters));if(raw===null)return;let params;try{params=JSON.parse(raw)}catch(_){return note('Parameters must be valid JSON.',true)}try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({parameters:params})});await load();note('FX parameters updated.')}catch(e){note(e.message,true)}}
async function delFx(id){try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx/${encodeURIComponent(id)}`,{method:'DELETE'});await load()}catch(e){note(e.message,true)}}
async function moveFx(index,delta){const ids=current.effects.map(x=>x.id),next=index+delta;if(next<0||next>=ids.length)return;[ids[index],ids[next]]=[ids[next],ids[index]];try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx/order`,{method:'PUT',body:JSON.stringify({effect_ids:ids})});await load()}catch(e){note(e.message,true)}}
$('track').onchange=load;$('add').onclick=async()=>{try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx`,{method:'POST',body:JSON.stringify({type:$('effect').value,parameters:{}})});await load();note('Effect added to the real render chain.')}catch(e){note(e.message,true)}};$('apply').onclick=async()=>{if(!$('preset').value)return;try{await api(`${root}/tracks/${encodeURIComponent($('track').value)}/fx/preset`,{method:'POST',body:JSON.stringify({preset_id:$('preset').value,replace_chain:true})});await load();note('Preset applied.')}catch(e){note(e.message,true)}};boot().catch(e=>note(e.message,true));
</script></body></html>"""


@router.get("/fx-lab", response_class=HTMLResponse, include_in_schema=False)
def fx_lab_page(request: Request):
    _member(request)
    return HTMLResponse(LAB_HTML, headers={"Cache-Control": "no-store"})


__all__ = ["router", "normalize_parameters", "PRESETS", "STANDARD_TYPES", "ADVANCED_TYPES"]
