from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .song_dna import SongDNAStore
from .tenant_storage import list_project_dirs, project_path

router = APIRouter()


CSS = r"""
:root{--bg:#03040a;--panel:#0c1020;--panel2:#111629;--line:#ffffff1c;--text:#fff;--muted:#b8bfd2;--gold:#e9c86f;--violet:#9a6dff;--cyan:#57dbff;--green:#6fe0a5;--bad:#ff8ca2}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 7% 0,#3a176566,transparent 29%),radial-gradient(circle at 95% 0,#153d8066,transparent 24%),linear-gradient(#020309,#070913 66%,#020309);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}a{color:inherit;text-decoration:none}.wrap{width:min(1500px,calc(100% - 28px));margin:auto}.nav{position:sticky;top:0;z-index:10;background:#05060bea;backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}.navin{min-height:70px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.65rem;letter-spacing:.09em;text-transform:uppercase}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:11px;padding:9px 12px;background:#ffffff08;color:#fff;font:inherit;font-weight:850;cursor:pointer}.btn.primary,button.primary{border:0;background:linear-gradient(115deg,var(--gold),var(--violet));color:#150d1d}.btn.good{border-color:#6fe0a544;color:var(--green)}.hero{padding:42px 0 18px}.eyebrow{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.5rem,6vw,5.4rem);line-height:.92;letter-spacing:-.055em;margin:.15em 0 .2em}.lead{max-width:1050px;color:var(--muted);line-height:1.6}.grid{display:grid;grid-template-columns:1.05fr 1fr;gap:12px}.card{border:1px solid var(--line);border-radius:19px;background:linear-gradient(145deg,#111628ed,#090c18f2);padding:16px}.project{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;margin:8px 0}.workspace{display:grid;grid-template-columns:260px 1fr;gap:12px;padding-bottom:55px}.side{position:sticky;top:86px;height:max-content}.tabs{display:grid;gap:6px}.tab{width:100%;text-align:left}.tab.active{border-color:var(--gold);background:#ffffff11}.panel{min-width:0}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.metric{border:1px solid var(--line);border-radius:14px;padding:12px;background:#ffffff05}.metric b{display:block;font-size:1.3rem}.muted{color:var(--muted)}.row{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}.section,.lyric,.instrument,.directive{border:1px solid var(--line);border-radius:14px;background:#ffffff05;padding:12px;margin:8px 0}.section.locked,.instrument.locked{border-color:#e9c86f55}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.7rem;margin:2px}.pill.good{color:var(--green);border-color:#6fe0a544}.field,textarea,select{width:100%;border:1px solid var(--line);border-radius:11px;padding:10px;background:#060912;color:#fff;font:inherit;outline:none}textarea{min-height:85px;resize:vertical}.field:focus,textarea:focus,select:focus{border-color:var(--gold)}.form{display:grid;gap:8px;margin-top:9px}.notice{display:none;border:1px solid var(--line);border-radius:12px;padding:10px;margin:12px 0}.notice.show{display:block}.notice.bad{color:#ffd8df;border-color:#ff8ca244}.audio{width:100%;margin-top:8px}.empty{padding:25px;text-align:center;border:1px dashed #ffffff2d;border-radius:15px;color:var(--muted)}.quality{border-left:4px solid var(--green);padding:12px 14px;background:#6fe0a50b;border-radius:12px}.footer{border-top:1px solid var(--line);padding:28px 0 45px;color:var(--muted);font-size:.82rem}@media(max-width:1050px){.workspace,.grid{grid-template-columns:1fr}.side{position:relative;top:auto}.tabs{grid-template-columns:repeat(4,1fr)}}@media(max-width:760px){.metrics{grid-template-columns:1fr 1fr}.tabs{grid-template-columns:1fr 1fr}.project{grid-template-columns:1fr}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"
    )


@router.get("/song-editor", response_class=HTMLResponse, include_in_schema=False)
def song_editor_index(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse("/signin?next=/song-editor", status_code=303)
    rows = []
    for project in list_project_dirs():
        dna_path = project / "song_dna.json"
        title = project.name
        meta = "Song DNA will be initialised when opened."
        if dna_path.is_file():
            try:
                dna = SongDNAStore(project).load()
                title = dna.title
                meta = f"v{dna.version} · {len(dna.sections)} sections · {len(dna.instruments)} instrument layers · {len(dna.lyric_lines)} lyric lines"
            except Exception:
                meta = "Song DNA exists but needs repair/reinitialisation."
        rows.append(
            f"<div class='project card'><div><b>{escape(title)}</b><div class='muted'>{escape(project.name)} · {escape(meta)}</div></div>"
            f"<a class='btn primary' href='/song-editor/{quote(project.name, safe='')}'>Open Song DNA</a></div>"
        )
    projects = "".join(rows) or "<div class='empty'>No music projects yet. Create a song from the Music Creation House first.</div>"
    body = f"""<nav class='nav'><div class='wrap navin'><a class='brand' href='/dashboard'>{escape(PRODUCT_FULL_NAME)}<small>Editable Song DNA</small></a><div><a class='btn' href='/daw'>DAW</a> <a class='btn' href='/dashboard'>Dashboard</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Release-grade music stays editable</div><h1>The master is a render.<br>The song is the project.</h1><p class='lead'>Open any song to edit stable lyric lines, sections and instrument layers. Changes are written as non-destructive Aura directives so the renderer can replace only the requested part and preserve the rest.</p></section>{projects}</main><footer class='footer'><div class='wrap'>Powered by Elevate Souls Productions & Aura AI Systems</div></footer>"""
    return _page("Editable Song DNA", body)


@router.get("/song-editor/{project_name}", response_class=HTMLResponse, include_in_schema=False)
def song_editor_project(project_name: str, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse(f"/signin?next=/song-editor/{quote(project_name, safe='')}", status_code=303)
    try:
        project_path(project_name, must_exist=True)
    except Exception:
        return _page("Project not found", "<main class='wrap'><div class='card'>Project not found.</div></main>")
    encoded = quote(project_name, safe="")
    body = r"""<nav class='nav'><div class='wrap navin'><a class='brand' href='/song-editor'>Pulsar-Frequency House<small>Editable Song DNA</small></a><div><a class='btn' href='/daw'>Open DAW</a> <a class='btn' href='/dashboard'>Dashboard</a></div></div></nav>
<main class='wrap'><section class='hero'><div class='eyebrow'>Ultra-real finished music · non-destructive editing</div><h1 id='title'>Loading Song DNA…</h1><p class='lead'>Change one lyric, one instrument or one section without telling Aura to rebuild everything else. Planned edits remain explicit until the configured music renderer has actually created the replacement audio.</p><div id='notice' class='notice'></div><div id='quality' class='quality'>Loading release-quality state…</div></section>
<section class='metrics' id='metrics'></section><section class='workspace' style='margin-top:12px'><aside class='card side'><div class='eyebrow'>Song controls</div><div class='tabs'><button class='tab active' data-tab='overview' onclick="switchTab('overview',this)">Overview</button><button class='tab' data-tab='lyrics' onclick="switchTab('lyrics',this)">Lyrics</button><button class='tab' data-tab='sections' onclick="switchTab('sections',this)">Sections</button><button class='tab' data-tab='instruments' onclick="switchTab('instruments',this)">Instruments</button><button class='tab' data-tab='directives' onclick="switchTab('directives',this)">Aura edits</button></div><hr style='border:0;border-top:1px solid var(--line);margin:14px 0'><button class='btn good' style='width:100%' onclick='syncSession()'>Sync DAW session</button><a class='btn' style='width:100%;margin-top:7px;text-align:center' href='/projects/__PROJECT__/outputs'>Output API</a></aside><section class='panel' id='panel'><div class='empty'>Loading…</div></section></section></main><footer class='footer'><div class='wrap'>Pulsar-Frequency House · Release-grade editable music · Powered by Aura AI Systems</div></footer>
<script>
const PROJECT='__PROJECT__'; const API=`/projects/${PROJECT}/song-dna`; let dna=null, outputs=[], active='overview';
const $=id=>document.getElementById(id); function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function notice(msg,bad=false){const n=$('notice');n.textContent=msg;n.className='notice show'+(bad?' bad':'');clearTimeout(window._nt);window._nt=setTimeout(()=>n.className='notice',6500)}
async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(e){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
async function load(){try{let d=await req(API);dna=d.song_dna;try{outputs=await req(`/projects/${PROJECT}/outputs`)}catch(e){outputs=[]}render()}catch(e){notice(e.message,true);$('panel').innerHTML='<div class="empty">Unable to load Song DNA.</div>'}}
function switchTab(t,el){active=t;document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));el.classList.add('active');renderPanel()}
function render(){ $('title').textContent=dna.title; $('metrics').innerHTML=`<div class="metric"><span class="muted">Version</span><b>${dna.version}</b></div><div class="metric"><span class="muted">BPM</span><b>${esc(dna.bpm??'Auto')}</b></div><div class="metric"><span class="muted">Key</span><b>${esc(dna.key||'Auto')}</b></div><div class="metric"><span class="muted">Sections</span><b>${dna.sections.length}</b></div><div class="metric"><span class="muted">Layers</span><b>${dna.instruments.length}</b></div>`; const master=outputs.find(x=>x.name==='Aura_Final_Master.wav'&&x.stream_url); $('quality').innerHTML=`<b>Release Quality Contract:</b> ${esc(dna.quality_contract?.standard||'release-grade editable master')} · real audio required · perceptual review required.${master?`<audio class="audio" controls src="${master.stream_url}"></audio>`:'<div class="muted">No streamable final master is currently available for this plan/project.</div>'}`; renderPanel()}
function renderPanel(){if(!dna)return;const f={overview:overview,lyrics:lyrics,sections:sections,instruments:instruments,directives:directives}[active]||overview;$('panel').innerHTML=f()}
function overview(){return `<div class="grid"><div class="card"><div class="eyebrow">Song DNA</div><h2>${esc(dna.title)}</h2><p class="muted">${esc(dna.genre||'')} ${dna.mood?'· '+esc(dna.mood):''} ${dna.language?'· '+esc(dna.language):''}</p><p>${esc(dna.structure_text||'Structure will be inferred from the project.')}</p><div><span class="pill">${esc(dna.vocal_mode)}</span>${dna.voice_profile_id?`<span class="pill good">approved voice profile</span>`:''}</div></div><div class="card"><div class="eyebrow">Editable contract</div><h2>Preserve what matters.</h2><p class="muted">Lyrics, sections and instrument identities have stable IDs. Aura edits can target one item while the rest of the project remains preserved or locked.</p><p class="muted">Renderer execution is kept separate from planning so the interface never pretends a new audio take exists until generation completes.</p></div></div>`}
function lyrics(){if(!dna.lyric_lines.length)return '<div class="empty">No lyrics in this project.</div>';return `<div class="card"><div class="eyebrow">Line-level lyric editing</div><h2>Rewrite only the line you choose.</h2><p class="muted">Saving a line updates the source lyrics and creates a local vocal-regeneration directive.</p>${dna.lyric_lines.map(l=>`<div class="lyric"><div class="row"><div><span class="pill">${esc(l.id)}</span><span class="pill">revision ${l.revision}</span></div></div><textarea id="txt_${l.id}">${esc(l.text)}</textarea><button class="btn primary" onclick="saveLyric('${l.id}')">Save line & plan local vocal repair</button></div>`).join('')}</div>`}
async function saveLyric(id){try{const text=$(`txt_${id}`).value.trim();const d=await req(`${API}/lyrics/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({text})});dna=d.song_dna;render();notice('Lyric updated. Local vocal regeneration is planned, not falsely marked rendered.')}catch(e){notice(e.message,true)}}
function sections(){return `<div class="card"><div class="eyebrow">Section-local regeneration</div><h2>Change one part of the arrangement.</h2>${dna.sections.map(s=>`<div class="section ${s.locked?'locked':''}"><div class="row"><div><b>${esc(s.name)}</b><div><span class="pill">${esc(s.id)}</span>${s.locked?'<span class="pill">locked</span>':''}</div></div></div><div class="form"><textarea id="sec_${s.id}" placeholder="Example: Make this bridge more intimate with only piano, bass and a restrained vocal build"></textarea><button class="btn primary" ${s.locked?'disabled':''} onclick="regenSection('${s.id}')">Plan section regeneration</button></div></div>`).join('')}</div>`}
async function regenSection(id){try{const instruction=$(`sec_${id}`).value.trim();const d=await req(`${API}/sections/${encodeURIComponent(id)}/regenerate-plan`,{method:'POST',body:JSON.stringify({instruction,preserve_instruments:true})});await load();active='directives';document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab==='directives'));renderPanel();notice('Section edit planned. Only this region should be regenerated.')}catch(e){notice(e.message,true)}}
function instruments(){return `<div class="card"><div class="eyebrow">Instrument replacement</div><h2>Swap a sound without rebuilding the song.</h2>${dna.instruments.length?dna.instruments.map(i=>`<div class="instrument ${i.locked?'locked':''}"><div class="row"><div><b>${esc(i.label)}</b><div><span class="pill">${esc(i.role)}</span><span class="pill">${esc(i.id)}</span>${i.track_id?'<span class="pill good">DAW linked</span>':''}${i.stem_ref?'<span class="pill good">stem linked</span>':''}</div></div></div><div class="form"><input class="field" id="rep_${i.id}" placeholder="Replacement, e.g. fingerpicked acoustic guitar"><textarea id="ins_${i.id}" placeholder="Optional performance direction"></textarea><button class="btn primary" ${i.locked?'disabled':''} onclick="replaceInstrument('${i.id}')">Plan instrument swap</button></div></div>`).join(''):'<div class="empty">Sync the DAW session or create instrument layers first.</div>'}</div>`}
async function replaceInstrument(id){try{const replacement=$(`rep_${id}`).value.trim(), instruction=$(`ins_${id}`).value.trim();await req(`${API}/instruments/${encodeURIComponent(id)}/replace-plan`,{method:'POST',body:JSON.stringify({replacement,instruction})});await load();active='directives';document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab==='directives'));renderPanel();notice('Instrument swap planned. The current layer remains until a replacement render succeeds.')}catch(e){notice(e.message,true)}}
function directives(){return `<div class="card"><div class="eyebrow">Aura edit queue</div><h2>Explicit non-destructive changes</h2>${dna.directives.length?dna.directives.slice().reverse().map(d=>`<div class="directive"><div class="row"><div><b>${esc(d.action.replaceAll('_',' '))}</b><div><span class="pill">${esc(d.status)}</span><span class="pill">${esc(d.renderer_route)}</span></div></div><small class="muted">${esc(d.created_at.slice(0,16).replace('T',' '))}</small></div><p>${esc(d.instruction)}</p><div class="muted">Targets: ${esc(d.target_ids.join(', ')||'—')}</div><div class="muted">Preserve: ${esc(d.preserve_ids.slice(0,10).join(', ')||'—')}${d.preserve_ids.length>10?' …':''}</div></div>`).join(''):'<div class="empty">No targeted edits have been planned yet.</div>'}</div>`}
async function syncSession(){try{const d=await req(`${API}/sync-session`,{method:'POST',body:'{}'});dna=d.song_dna;render();notice('DAW tracks, effects and automation references synced into Song DNA.')}catch(e){notice(e.message,true)}}
load();
</script>""".replace("__PROJECT__", encoded)
    return _page("Song DNA Editor", body)
