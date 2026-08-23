from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import PRODUCT_NAME
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY, get_plan

router = APIRouter()
store = AccountStore()


@router.get('/history', response_class=HTMLResponse)
def history_portal(request: Request):
    user = store.resolve_session(request.cookies.get('lss_session'))
    if not user:
        return RedirectResponse('/signin', status_code=303)
    if user.get('status') != 'active':
        return RedirectResponse('/dashboard', status_code=303)
    plan = get_plan(user.get('plan_id') or 'free')
    enabled = plan.has(REVISION_HISTORY)
    limit = 200 if plan.has(DEEP_REVISION_HISTORY) else 20 if enabled else 0
    name = escape(user.get('display_name') or 'Member')
    locked = '' if enabled else "<div class='alert'>Revision history unlocks on Base. Pro expands the history to 200 metadata/session snapshots.</div>"
    page = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Project History — {escape(PRODUCT_NAME)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,system-ui,sans-serif;color:#fff}}.wrap{{max-width:1180px;margin:auto;padding:22px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.brand{{font-size:1.15rem;font-weight:950}}a{{color:inherit;text-decoration:none}}.btn,button{{border:1px solid #523263;background:#21102c;color:#fff;padding:10px 13px;border-radius:10px;font-weight:850;cursor:pointer}}.primary{{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#150b18}}.panel,.card{{background:#140b1b;border:1px solid #523263;border-radius:17px;padding:16px}}.layout{{display:grid;grid-template-columns:280px 1fr;gap:16px;margin-top:20px}}select,input{{width:100%;background:#08050c;color:#fff;border:1px solid #523263;border-radius:9px;padding:11px}}.field{{margin:12px 0}}.muted{{color:#cdbfd4}}.revision{{padding:13px;border:1px solid #362441;border-radius:12px;margin:9px 0}}.revision h3{{margin:0 0 5px}}.row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.status,.alert{{white-space:pre-wrap;background:#09060c;border:1px solid #4b304f;border-radius:10px;padding:12px;margin-top:12px}}@media(max-width:800px){{.layout{{grid-template-columns:1fr}}}}
</style></head><body><div class='wrap'><div class='top'><a class='brand' href='/studio'>{escape(PRODUCT_NAME)}<small style='display:block;color:#e7b953'>ESP PROJECT HISTORY</small></a><div class='row'><span>{escape(plan.name)} · {limit or 'Locked'} revisions</span><a class='btn' href='/studio'>← Studio</a><a class='btn' href='/production-suite'>Production Suite</a></div></div>
<div class='layout'><aside class='panel'><h2>History</h2><p class='muted'>Hello {name}. Snapshots preserve project/session metadata without duplicating your large audio files.</p>{locked}<div class='field'><label>Project</label><select id='project' onchange='loadHistory()'></select></div><div class='field'><label>Snapshot label</label><input id='label' value='Manual checkpoint'></div><button class='primary' onclick='snapshot()' {'disabled' if not enabled else ''}>Create snapshot</button><div id='status' class='status'>Choose a project.</div></aside><main class='panel'><h2>Revision timeline</h2><p class='muted'>Restoring automatically creates a backup of the current state first.</p><div id='revisions'></div></main></div></div>
<script>
const ENABLED={str(enabled).lower()};
async function api(url,options={{}}){{const r=await fetch(url,{{credentials:'same-origin',...options}});let b;try{{b=await r.json()}}catch{{b=await r.text()}}if(!r.ok)throw new Error(typeof b==='string'?b:(b.detail||JSON.stringify(b)));return b}}
function status(v,ok=true){{const e=document.getElementById('status');e.textContent=typeof v==='string'?v:JSON.stringify(v,null,2);e.style.color=ok?'#86e0a8':'#ff9aa9'}}
async function init(){{try{{const ps=await api('/projects');const s=document.getElementById('project');for(const p of ps){{const o=document.createElement('option');o.value=p.name;o.textContent=p.name;s.appendChild(o)}}if(ps.length&&ENABLED)loadHistory();else if(!ps.length)status('Create a project first.')}}catch(e){{status(e.message,false)}}}}
async function loadHistory(){{if(!ENABLED)return;const p=document.getElementById('project').value;if(!p)return;try{{const data=await api('/projects/'+encodeURIComponent(p)+'/revisions');const root=document.getElementById('revisions');root.innerHTML='';if(!data.revisions.length)root.innerHTML='<div class="revision muted">No snapshots yet.</div>';for(const rev of data.revisions){{const d=document.createElement('div');d.className='revision';const title=document.createElement('h3');title.textContent=rev.label||rev.id;const meta=document.createElement('div');meta.className='muted';meta.textContent=(rev.created_at||'')+' · '+(rev.reason||'')+' · '+(rev.files||[]).length+' metadata files';const b=document.createElement('button');b.textContent='Restore this revision';b.onclick=()=>restoreRev(rev.id);d.append(title,meta,b);root.appendChild(d)}}status('Loaded '+data.revisions.length+' revisions · limit '+data.limit)}}catch(e){{status(e.message,false)}}}}
async function snapshot(){{const p=document.getElementById('project').value;if(!p)return;try{{const r=await api('/projects/'+encodeURIComponent(p)+'/revisions',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{label:document.getElementById('label').value,reason:'manual'}})}});status(r);loadHistory()}}catch(e){{status(e.message,false)}}}}
async function restoreRev(id){{const p=document.getElementById('project').value;if(!confirm('Restore this project revision? Aura will first snapshot your current state.'))return;try{{status('Restoring...');status(await api('/projects/'+encodeURIComponent(p)+'/revisions/'+encodeURIComponent(id)+'/restore',{{method:'POST'}}));loadHistory()}}catch(e){{status(e.message,false)}}}}
init();
</script></body></html>"""
    return HTMLResponse(page)
