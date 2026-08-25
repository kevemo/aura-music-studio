from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .project import ProjectWorkspace
from .song_dna import SongDNA, SongDNAStore, ensure_song_dna_from_manifest
from .tenant_storage import project_path

router = APIRouter(tags=["Song DNA Preserve Locks"])
LockKind = Literal["lyrics", "sections", "instruments"]


class LockPatch(BaseModel):
    locked: bool


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


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


def _items(dna: SongDNA, kind: LockKind):
    if kind == "lyrics":
        return dna.lyric_lines
    if kind == "sections":
        return dna.sections
    if kind == "instruments":
        return dna.instruments
    raise ValueError(f"Unsupported Song DNA lock kind: {kind}")


def _label(kind: LockKind, item) -> str:
    if kind == "lyrics":
        return item.text
    if kind == "sections":
        return item.name
    return item.label


def _event(dna: SongDNA, *, kind: LockKind, target_id: str, locked: bool, bulk: bool = False) -> None:
    history = list(dna.metadata.get("lock_events") or [])
    history.append(
        {
            "kind": kind,
            "target_id": target_id,
            "locked": bool(locked),
            "bulk": bool(bulk),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    dna.metadata["lock_events"] = history[-100:]


def set_song_lock(project_name: str, kind: LockKind, target_id: str, locked: bool) -> SongDNA:
    store = _store(project_name)
    dna = store.load()
    item = next((row for row in _items(dna, kind) if row.id == target_id), None)
    if item is None:
        raise KeyError(target_id)
    value = bool(locked)
    if item.locked == value:
        return dna
    item.locked = value
    dna.version += 1
    _event(dna, kind=kind, target_id=target_id, locked=value)
    return store.save(dna)


def set_all_song_locks(project_name: str, kind: LockKind, locked: bool) -> SongDNA:
    store = _store(project_name)
    dna = store.load()
    value = bool(locked)
    changed = False
    for item in _items(dna, kind):
        if item.locked != value:
            item.locked = value
            changed = True
    if not changed:
        return dna
    dna.version += 1
    _event(dna, kind=kind, target_id="*", locked=value, bulk=True)
    return store.save(dna)


def lock_snapshot(dna: SongDNA) -> dict:
    result: dict = {"version": dna.version, "project_name": dna.project_name, "title": dna.title}
    for kind in ("lyrics", "sections", "instruments"):
        rows = []
        for item in _items(dna, kind):
            row = {
                "id": item.id,
                "label": _label(kind, item),
                "locked": bool(item.locked),
            }
            if kind == "lyrics":
                row["section_id"] = item.section_id
                row["revision"] = item.revision
            elif kind == "sections":
                row["order"] = item.order
                row["kind"] = item.kind
            else:
                row["role"] = item.role
                row["enabled"] = bool(item.enabled)
            rows.append(row)
        result[kind] = rows
        result[f"{kind}_locked"] = sum(1 for row in rows if row["locked"])
    result["non_destructive"] = True
    result["audio_modified"] = False
    return result


@router.get("/projects/{project_name}/song-dna/locks")
def get_song_locks(project_name: str, request: Request):
    _member(request)
    try:
        return lock_snapshot(_store(project_name).load())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Unable to load Song DNA locks: {type(exc).__name__}: {exc}") from exc


@router.patch("/projects/{project_name}/song-dna/locks/{kind}/{target_id}")
def update_song_lock(project_name: str, kind: LockKind, target_id: str, patch: LockPatch, request: Request):
    _member(request)
    try:
        dna = set_song_lock(project_name, kind, target_id, patch.locked)
    except KeyError as exc:
        raise HTTPException(404, "Song DNA target not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "song_dna": dna.model_dump(mode="json"),
        "locks": lock_snapshot(dna),
        "audio_modified": False,
        "detail": "Preserve lock updated. No audio was regenerated or overwritten.",
    }


@router.put("/projects/{project_name}/song-dna/locks/{kind}")
def update_all_song_locks(project_name: str, kind: LockKind, patch: LockPatch, request: Request):
    _member(request)
    try:
        dna = set_all_song_locks(project_name, kind, patch.locked)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "song_dna": dna.model_dump(mode="json"),
        "locks": lock_snapshot(dna),
        "audio_modified": False,
        "detail": "Bulk preserve locks updated. No audio was regenerated or overwritten.",
    }


LOCKS_CSS = r"""
:root{--bg:#03040a;--panel:#0b0f1d;--line:#ffffff1d;--text:#fff;--muted:#b7bfd2;--gold:#edca72;--violet:#9d70ff;--cyan:#58ddff;--green:#74dfa8;--red:#ff90a4}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 8% 0,#3b1766,transparent 30%),radial-gradient(circle at 94% 0,#123e72,transparent 27%),#03040a;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}a{color:inherit;text-decoration:none}button{font:inherit}.wrap{width:min(1320px,calc(100% - 28px));margin:auto}.nav{border-bottom:1px solid var(--line);background:#05070de8;backdrop-filter:blur(18px);position:sticky;top:0;z-index:10}.navin{min-height:70px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.64rem;text-transform:uppercase;letter-spacing:.08em}.btn{display:inline-block;border:1px solid var(--line);border-radius:11px;padding:9px 12px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(115deg,var(--gold),var(--violet));color:#150d1e}.hero{padding:48px 0 22px}.eyebrow{font-size:.7rem;letter-spacing:.17em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.6rem);line-height:.92;letter-spacing:-.06em;margin:.15em 0 .2em}.lead{color:var(--muted);max-width:960px;line-height:1.6}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:18px 0}.metric{border:1px solid var(--line);border-radius:15px;padding:13px;background:#ffffff06}.metric b{display:block;font-size:1.45rem}.groups{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding-bottom:55px}.group{border:1px solid var(--line);border-radius:20px;background:linear-gradient(145deg,#11162a,#080b15);padding:15px;min-width:0}.groupHead{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;flex-wrap:wrap}.actions{display:flex;gap:5px;flex-wrap:wrap}.row{border:1px solid var(--line);border-radius:13px;padding:10px;margin:7px 0;background:#ffffff04}.row.locked{border-color:#edca7255;background:#edca7209}.rowTop{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.label{font-weight:800;line-height:1.4;word-break:break-word}.meta{font-size:.68rem;color:var(--muted);margin-top:4px}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.66rem}.pill.lock{color:var(--gold);border-color:#edca7250}.pill.open{color:var(--green);border-color:#74dfa84c}.small{font-size:.69rem;padding:6px 8px}.notice{display:none;border:1px solid var(--line);border-radius:11px;padding:10px 12px;margin:10px 0}.notice.show{display:block}.notice.bad{border-color:#ff90a455;color:#ffd7de}.empty{padding:20px;text-align:center;color:var(--muted);border:1px dashed #ffffff2a;border-radius:13px}.footer{border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:.8rem}@media(max-width:1000px){.groups{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}}@media(max-width:600px){.summary{grid-template-columns:1fr}}
"""

LOCKS_SCRIPT = r"""
const PROJECT=__PROJECT_JSON__;const API=`/projects/${encodeURIComponent(PROJECT)}/song-dna/locks`;let state=null;const $=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show'+(b?' bad':'');clearTimeout(window._t);window._t=setTimeout(()=>n.className='notice',5000)}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function title(kind){return kind==='lyrics'?'Lyric lines':kind==='sections'?'Song sections':'Instrument layers'}function icon(kind){return kind==='lyrics'?'✍️':kind==='sections'?'🎼':'🎸'}function render(){if(!state)return;document.title=`Preserve Locks — ${state.title}`;$('songTitle').textContent=state.title;$('summary').innerHTML=`<div class="metric"><span class="meta">Song DNA version</span><b>${state.version}</b></div><div class="metric"><span class="meta">Locked lyrics</span><b>${state.lyrics_locked}/${state.lyrics.length}</b></div><div class="metric"><span class="meta">Locked sections</span><b>${state.sections_locked}/${state.sections.length}</b></div><div class="metric"><span class="meta">Locked instruments</span><b>${state.instruments_locked}/${state.instruments.length}</b></div>`;$('groups').innerHTML=['lyrics','sections','instruments'].map(group=>`<section class="group"><div class="groupHead"><div><div class="eyebrow">${icon(group)} ${title(group)}</div><p class="meta">Locked targets cannot be directly edited or regenerated.</p></div><div class="actions"><button class="btn small" onclick="bulk('${group}',true)">Lock all</button><button class="btn small" onclick="bulk('${group}',false)">Unlock all</button></div></div>${state[group].length?state[group].map(row=>`<div class="row ${row.locked?'locked':''}"><div class="rowTop"><div style="min-width:0"><div class="label">${esc(row.label)}</div><div class="meta">${esc(row.id)}${row.role?' · '+esc(row.role):''}${row.kind?' · '+esc(row.kind):''}</div></div><div style="text-align:right"><span class="pill ${row.locked?'lock':'open'}">${row.locked?'LOCKED':'EDITABLE'}</span><div style="margin-top:5px"><button class="btn small" onclick="toggle('${group}','${esc(row.id)}',${!row.locked})">${row.locked?'Unlock':'Lock'}</button></div></div></div></div>`).join(''):'<div class="empty">Nothing in this category yet.</div>'}</section>`).join('')}async function load(){try{state=await req(API);render()}catch(e){note(e.message,true)}}async function toggle(kind,id,locked){try{const d=await req(`${API}/${kind}/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({locked})});state=d.locks;render();note(d.detail||'Preserve lock updated.')}catch(e){note(e.message,true)}}async function bulk(kind,locked){try{const d=await req(`${API}/${kind}`,{method:'PUT',body:JSON.stringify({locked})});state=d.locks;render();note(d.detail||'Preserve locks updated.')}catch(e){note(e.message,true)}}load();
"""


@router.get("/song-editor/{project_name}/locks", response_class=HTMLResponse, include_in_schema=False)
def song_lock_manager(project_name: str, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse(f"/signin?next=/song-editor/{quote(project_name, safe='')}/locks", status_code=303)
    try:
        _project(project_name)
    except HTTPException:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)
    project_json = json.dumps(project_name)
    script = LOCKS_SCRIPT.replace("__PROJECT_JSON__", project_json)
    back = f"/song-editor/{quote(project_name, safe='')}"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Preserve Locks — {escape(PRODUCT_FULL_NAME)}</title><style>{LOCKS_CSS}</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='{back}'>{escape(PRODUCT_FULL_NAME)}<small>Song DNA Preserve Locks</small></a><div><a class='btn' href='{back}'>Back to Song DNA</a> <a class='btn' href='/daw'>DAW</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Non-destructive exact-edit control</div><h1>Protect what Aura must <span style='color:var(--gold)'>not change.</span></h1><p class='lead'>Lock lyric lines, song sections or instrument layers before planning an edit. Locks are enforced by Song DNA edit methods and are included in preservation logic. Changing a lock edits control metadata only—no audio is generated, replaced or deleted.</p><h2 id='songTitle'>Loading…</h2><div id='notice' class='notice'></div></section><section id='summary' class='summary'></section><section id='groups' class='groups'></section></main><footer class='footer'><div class='wrap'>{escape(TAGLINE)} · {escape(ENDORSEMENT)}</div></footer><script>{script}</script></body></html>"""
    return HTMLResponse(html)


__all__ = ["router", "set_song_lock", "set_all_song_locks", "lock_snapshot"]
