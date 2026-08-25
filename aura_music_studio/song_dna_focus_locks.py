from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME
from .song_dna_locks import LockKind, _items, _store, lock_snapshot

router = APIRouter(tags=["Song DNA Focus Locks"])


def focus_song_locks(project_name: str, kind: LockKind, target_id: str):
    store = _store(project_name)
    dna = store.load()
    target = next((item for item in _items(dna, kind) if item.id == target_id), None)
    if target is None:
        raise KeyError(target_id)

    target_section_id = target.section_id if kind == "lyrics" else target.id if kind == "sections" else None
    changed = False

    for line in dna.lyric_lines:
        editable = (kind == "lyrics" and line.id == target_id) or (kind == "sections" and line.section_id == target_section_id)
        desired = not editable
        if line.locked != desired:
            line.locked = desired
            changed = True

    for section in dna.sections:
        editable = kind == "sections" and section.id == target_id
        # A parent section may remain locked while one exact lyric line is edited; lyric-line edits
        # are governed by the line lock itself and the locked section communicates preservation intent.
        desired = not editable
        if section.locked != desired:
            section.locked = desired
            changed = True

    for instrument in dna.instruments:
        editable = kind == "instruments" and instrument.id == target_id
        desired = not editable
        if instrument.locked != desired:
            instrument.locked = desired
            changed = True

    if changed:
        dna.version += 1
        events = list(dna.metadata.get("focus_lock_events") or [])
        events.append(
            {
                "kind": kind,
                "target_id": target_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "mode": "protect_everything_except_target",
                "audio_modified": False,
            }
        )
        dna.metadata["focus_lock_events"] = events[-100:]
        store.save(dna)
    return dna


@router.post("/projects/{project_name}/song-dna/locks/focus/{kind}/{target_id}")
def focus_lock(project_name: str, kind: LockKind, target_id: str, request: Request):
    if getattr(request.state, "member", None) is None:
        raise HTTPException(401, "Sign in required")
    try:
        dna = focus_song_locks(project_name, kind, target_id)
    except KeyError as exc:
        raise HTTPException(404, "Song DNA target not found") from exc
    return {
        "locks": lock_snapshot(dna),
        "target": {"kind": kind, "id": target_id},
        "audio_modified": False,
        "detail": "Everything except the selected Song DNA target is now protected. No audio was regenerated or overwritten.",
    }


CSS = r"""
:root{--line:#ffffff1d;--muted:#bac2d5;--gold:#efcc77;--violet:#9b6fff;--green:#76dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#3b1768,transparent 30%),radial-gradient(circle at 94% 0,#123e72,transparent 26%),#03040a;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1050px,calc(100% - 28px));margin:auto}a{color:inherit;text-decoration:none}.nav{border-bottom:1px solid var(--line);background:#05070dec}.navin{min-height:70px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.65rem;text-transform:uppercase}.hero{padding:48px 0 22px}.eyebrow{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5rem);line-height:.94;letter-spacing:-.055em;margin:.15em 0 .2em}.lead,.muted{color:var(--muted);line-height:1.55}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#11162a,#080b15);padding:16px;margin:10px 0}.row{display:grid;grid-template-columns:170px 1fr auto;gap:8px;align-items:center}.field{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#060912;color:#fff;font:inherit}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1e}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}.footer{border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:.8rem}@media(max-width:720px){.row{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const PROJECT=__PROJECT__,API=`/projects/${encodeURIComponent(PROJECT)}/song-dna/locks`,state=__STATE__,$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':''}function items(kind){return state[kind]||[]}function sync(){const kind=$('kind').value,$t=$('target');$t.innerHTML=items(kind).map(r=>`<option value="${esc(r.id)}">${esc(r.label)} · ${esc(r.id)}</option>`).join('')||'<option value="">Nothing available</option>';$('explain').textContent=kind==='sections'?'The selected section and its lyric lines remain editable. All other sections, lyric lines and instruments become protected.':kind==='lyrics'?'Only the selected lyric line remains editable. Sections and instruments are protected.':'Only the selected instrument layer remains editable. Lyrics and song sections are protected.'}async function apply(){const kind=$('kind').value,id=$('target').value;if(!id)return note('Choose a Song DNA target.',true);if(!confirm('Protect every other Song DNA target and leave only this target editable?'))return;try{const r=await fetch(`${API}/focus/${kind}/${encodeURIComponent(id)}`,{method:'POST',credentials:'same-origin'}),d=await r.json();if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);note(d.detail);setTimeout(()=>location.href=`/song-editor/${encodeURIComponent(PROJECT)}/locks`,900)}catch(e){note(e.message,true)}}$('kind').onchange=sync;$('apply').onclick=apply;sync();
"""


@router.get("/song-editor/{project_name}/focus-locks", response_class=HTMLResponse, include_in_schema=False)
def focus_locks_page(project_name: str, request: Request):
    if getattr(request.state, "member", None) is None:
        return RedirectResponse(f"/signin?next=/song-editor/{quote(project_name, safe='')}/focus-locks", status_code=303)
    try:
        snapshot = lock_snapshot(_store(project_name).load())
    except Exception:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)
    script = SCRIPT.replace("__PROJECT__", json.dumps(project_name)).replace("__STATE__", json.dumps(snapshot, ensure_ascii=False).replace("</", "<\\/"))
    encoded = quote(project_name, safe="")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Focus Locks — {escape(PRODUCT_FULL_NAME)}</title><style>{CSS}</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/song-editor/{encoded}'>{escape(PRODUCT_FULL_NAME)}<small>Song DNA Focus Protection</small></a><div><a class='btn' href='/song-editor/{encoded}/locks'>Preserve Locks</a> <a class='btn' href='/song-editor/{encoded}'>Song DNA</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Surgical edit boundary</div><h1>Protect everything <span style='color:var(--gold)'>except this.</span></h1><p class='lead'>Choose exactly one lyric line, section or instrument layer. Aura will mark every other relevant Song DNA target as protected before you plan the edit. This changes lock metadata only—no audio is generated, deleted or overwritten.</p><div id='notice' class='notice'></div></section><section class='card'><div class='row'><select id='kind' class='field'><option value='lyrics'>Lyric line</option><option value='sections'>Song section</option><option value='instruments'>Instrument layer</option></select><select id='target' class='field'></select><button id='apply' class='btn primary'>Protect everything else</button></div><p id='explain' class='muted'></p></section></main><footer class='footer'><div class='wrap'>{escape(ENDORSEMENT)}</div></footer><script>{script}</script></body></html>"""
    return HTMLResponse(html)


__all__ = ["router", "focus_song_locks"]
