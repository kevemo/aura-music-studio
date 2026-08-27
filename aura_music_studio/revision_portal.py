from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import PRODUCT_NAME
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY, get_plan

router = APIRouter()
store = AccountStore()


@router.get("/history", response_class=HTMLResponse)
def history_portal(request: Request):
    user = store.resolve_session(request.cookies.get("lss_session"))
    if not user:
        return RedirectResponse("/signin", status_code=303)
    if user.get("status") != "active":
        return RedirectResponse("/dashboard", status_code=303)

    plan = get_plan(user.get("plan_id") or "free")
    enabled = plan.has(REVISION_HISTORY)
    deep = plan.has(DEEP_REVISION_HISTORY)
    limit = 200 if deep else 20 if enabled else 0
    name = escape(user.get("display_name") or "Member")
    locked = (
        ""
        if enabled
        else "<div class='alert'>Revision history unlocks on Basic. Pro expands history to 200 cross-editor checkpoints and adds A/B comparison.</div>"
    )
    compare_note = (
        "Select any two checkpoints to see which editor domains and metadata files changed."
        if deep
        else "Cross-editor A/B checkpoint comparison unlocks on Pro. Basic still preserves and restores complete supported checkpoints."
    )

    page = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project History — __PRODUCT__</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;color:#fff;background:radial-gradient(circle at 20% 0,#351442 0,#120817 34%,#07040a 75%);min-height:100vh}.wrap{max-width:1240px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}.brand{font-size:1.15rem;font-weight:950}a{color:inherit;text-decoration:none}.btn,button{border:1px solid #523263;background:#21102c;color:#fff;padding:10px 13px;border-radius:10px;font-weight:850;cursor:pointer}.btn:hover,button:hover{border-color:#bd8430}.primary{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#150b18;border:0}.panel,.card{background:#140b1beF;border:1px solid #523263;border-radius:17px;padding:16px}.layout{display:grid;grid-template-columns:310px 1fr;gap:16px;margin-top:20px}select,input{width:100%;background:#08050c;color:#fff;border:1px solid #523263;border-radius:9px;padding:11px}.field{margin:12px 0}.muted{color:#cdbfd4}.revision{padding:14px;border:1px solid #362441;border-radius:13px;margin:10px 0;background:#0d0812}.revision h3{margin:0 0 5px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status,.alert,.truth{white-space:pre-wrap;background:#09060c;border:1px solid #4b304f;border-radius:10px;padding:12px;margin-top:12px}.truth{font-size:.78rem;color:#cdbfd4}.compare{margin-bottom:14px;background:#12091a;border:1px solid #523263;border-radius:14px;padding:14px}.compare-grid{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:end}.compare-result{margin-top:10px;padding:12px;border:1px solid #362441;border-radius:11px;background:#09060c}.badge-row{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.badge{display:inline-flex;align-items:center;border:1px solid #6e447b;border-radius:999px;padding:4px 8px;font-size:.68rem;font-weight:850;color:#ead9f0;background:#1c1025}.badge.daw{border-color:#7d63aa}.badge.editor{border-color:#bd8430}.badge.manifest{border-color:#568e83}.badge.meta{border-color:#6f6f8f}.badge.core{border-color:#8e6556}.revision-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.revision-actions button{padding:7px 9px;font-size:.74rem}.checkpoint{font-size:.7rem;color:#86e0a8;font-weight:850}.compare-files{margin:8px 0 0;padding-left:18px;color:#cdbfd4;font-size:.76rem}.section-title{display:flex;justify-content:space-between;gap:12px;align-items:start;flex-wrap:wrap}.section-title h2{margin:0}.small{font-size:.72rem}.disabled{opacity:.58}button:disabled,select:disabled{opacity:.5;cursor:not-allowed}@media(max-width:850px){.layout{grid-template-columns:1fr}.compare-grid{grid-template-columns:1fr}.wrap{padding:14px}}
</style></head><body>
<div class="wrap"><div class="top"><a class="brand" href="/studio">__PRODUCT__<small style="display:block;color:#e7b953">ESP CROSS-EDITOR PROJECT HISTORY</small></a><div class="row"><span>__PLAN__ · __LIMIT__ revisions</span><a class="btn" href="/studio">← Studio</a><a class="btn" href="/daw">Visual DAW</a><a class="btn" href="/creative/editor-workspace">Image / Video Editor</a><a class="btn" href="/production-suite">Production Suite</a></div></div>
<div class="layout"><aside class="panel"><h2>History</h2><p class="muted">Hello __NAME__. Each supported checkpoint can preserve Music/DAW, Creative Project and professional Image/Video editor state together without copying large media binaries.</p>__LOCKED__<div class="field"><label>Project</label><select id="project" onchange="loadHistory()"></select></div><div class="field"><label>Checkpoint label</label><input id="label" value="Manual cross-editor checkpoint"></div><button class="primary" onclick="snapshot()" __SNAPSHOT_DISABLED__>Create checkpoint</button><div class="truth"><b>Non-destructive by design</b><br>Audio, image and video source files remain referenced in place. Restoring a checkpoint first creates a backup of the current supported project state.</div><div id="status" class="status">Choose a project.</div></aside>
<main class="panel"><div class="section-title"><div><h2>Revision timeline</h2><p class="muted">See exactly which creative domains each checkpoint contains, restore the full supported state, or compare two Pro checkpoints.</p></div><span id="checkpointTruth" class="checkpoint"></span></div>
<div id="comparePanel" class="compare __COMPARE_CLASS__"><h3 style="margin:0 0 5px">A/B checkpoint comparison</h3><p class="muted">__COMPARE_NOTE__</p><div class="compare-grid"><div class="field"><label>Checkpoint A</label><select id="compareA" __COMPARE_DISABLED__></select></div><div class="field"><label>Checkpoint B</label><select id="compareB" __COMPARE_DISABLED__></select></div><button id="compareButton" class="primary" onclick="compareRevisions()" __COMPARE_DISABLED__>Compare A → B</button></div><div id="compareResult" class="compare-result muted">Select two checkpoints.</div></div>
<div id="revisions"></div></main></div></div>
<script>
const ENABLED=__ENABLED__;
const DEEP=__DEEP__;
const DOMAIN_LABELS={music_daw:'Music / DAW',creative_manifest:'Creative Project',professional_image_video_editor:'Image + Video Editor',production_metadata:'Production Metadata',project_core:'Project Core'};
const DOMAIN_CLASSES={music_daw:'daw',creative_manifest:'manifest',professional_image_video_editor:'editor',production_metadata:'meta',project_core:'core'};
let currentRevisions=[];
async function api(url,options={}){const r=await fetch(url,{credentials:'same-origin',...options});let b;try{b=await r.json()}catch{b=await r.text()}if(!r.ok)throw new Error(typeof b==='string'?b:(b.detail||JSON.stringify(b)));return b}
function status(v,ok=true){const e=document.getElementById('status');e.textContent=typeof v==='string'?v:JSON.stringify(v,null,2);e.style.color=ok?'#86e0a8':'#ff9aa9'}
function domainKeys(rev){return Object.keys(rev?.domains||{})}
function addDomainBadges(root,domains){const row=document.createElement('div');row.className='badge-row';for(const domain of domains){const badge=document.createElement('span');badge.className='badge '+(DOMAIN_CLASSES[domain]||'meta');badge.textContent=DOMAIN_LABELS[domain]||domain;row.appendChild(badge)}if(!domains.length){const badge=document.createElement('span');badge.className='badge meta';badge.textContent='Legacy metadata checkpoint';row.appendChild(badge)}root.appendChild(row)}
function revisionLabel(rev){const date=rev.created_at?new Date(rev.created_at).toLocaleString():'';return (rev.label||rev.id)+(date?' · '+date:'')}
function populateCompare(rows){const a=document.getElementById('compareA'),b=document.getElementById('compareB');const oldA=a.value,oldB=b.value;a.innerHTML='';b.innerHTML='';for(const rev of rows){for(const select of [a,b]){const option=document.createElement('option');option.value=rev.id;option.textContent=revisionLabel(rev);select.appendChild(option)}}if([...a.options].some(o=>o.value===oldA))a.value=oldA;if([...b.options].some(o=>o.value===oldB))b.value=oldB;if(!oldB&&b.options.length>1)b.selectedIndex=1}
function setCompare(which,id){const select=document.getElementById(which==='A'?'compareA':'compareB');if(select)select.value=id;document.getElementById('compareResult').textContent='Checkpoint '+which+' selected. Choose the other checkpoint and compare.'}
function renderRevision(rev){const d=document.createElement('div');d.className='revision';const title=document.createElement('h3');title.textContent=rev.label||rev.id;const meta=document.createElement('div');meta.className='muted small';meta.textContent=(rev.created_at||'')+' · '+(rev.reason||'')+' · '+(rev.files||[]).length+' metadata files';d.append(title,meta);addDomainBadges(d,domainKeys(rev));if(rev.cross_editor_checkpoint){const truth=document.createElement('div');truth.className='checkpoint';truth.textContent='✓ Cross-editor checkpoint · source media not duplicated';d.appendChild(truth)}const actions=document.createElement('div');actions.className='revision-actions';const restore=document.createElement('button');restore.textContent='Restore full checkpoint';restore.onclick=()=>restoreRev(rev.id);actions.appendChild(restore);if(DEEP){const a=document.createElement('button');a.textContent='Use as A';a.onclick=()=>setCompare('A',rev.id);const b=document.createElement('button');b.textContent='Use as B';b.onclick=()=>setCompare('B',rev.id);actions.append(a,b)}d.appendChild(actions);return d}
async function init(){try{const ps=await api('/projects');const s=document.getElementById('project');for(const p of ps){const o=document.createElement('option');o.value=p.name;o.textContent=p.name;s.appendChild(o)}if(ps.length&&ENABLED)loadHistory();else if(!ps.length)status('Create a project first.')}catch(e){status(e.message,false)}}
async function loadHistory(){if(!ENABLED)return;const p=document.getElementById('project').value;if(!p)return;try{const data=await api('/projects/'+encodeURIComponent(p)+'/revisions');currentRevisions=data.revisions||[];const root=document.getElementById('revisions');root.innerHTML='';if(!currentRevisions.length)root.innerHTML='<div class="revision muted">No checkpoints yet.</div>';for(const rev of currentRevisions)root.appendChild(renderRevision(rev));populateCompare(currentRevisions);document.getElementById('checkpointTruth').textContent=data.cross_editor_checkpoints?'✓ Cross-editor checkpoint engine active':'';status('Loaded '+currentRevisions.length+' checkpoints · limit '+data.limit+' · media binaries are not duplicated.')}catch(e){status(e.message,false)}}
async function snapshot(){const p=document.getElementById('project').value;if(!p)return;try{const r=await api('/projects/'+encodeURIComponent(p)+'/revisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:document.getElementById('label').value,reason:'manual'})});status('Checkpoint created · '+(r.label||r.id));loadHistory()}catch(e){status(e.message,false)}}
async function restoreRev(id){const p=document.getElementById('project').value;if(!confirm('Restore this complete supported project checkpoint? Aura will first snapshot your current state.'))return;try{status('Restoring cross-editor checkpoint...');const result=await api('/projects/'+encodeURIComponent(p)+'/revisions/'+encodeURIComponent(id)+'/restore',{method:'POST'});status('Restored '+result.restored_revision+' · domains: '+(result.restored_domains||[]).map(v=>DOMAIN_LABELS[v]||v).join(', ')+' · source media unchanged.');loadHistory()}catch(e){status(e.message,false)}}
function renderComparison(data){const root=document.getElementById('compareResult');root.innerHTML='';const title=document.createElement('b');title.textContent=(data.left?.label||data.left?.id||'A')+' → '+(data.right?.label||data.right?.id||'B');root.appendChild(title);addDomainBadges(root,data.domains_changed||[]);const files=data.files||{};const summary=document.createElement('div');summary.className='small';summary.textContent='Changed '+(files.changed||[]).length+' · Added '+(files.added||[]).length+' · Removed '+(files.removed||[]).length+' · Unchanged '+(files.unchanged||[]).length+' metadata files';root.appendChild(summary);const changed=[...(files.changed||[]),...(files.added||[]),...(files.removed||[])];if(changed.length){const details=document.createElement('details');const heading=document.createElement('summary');heading.textContent='Changed checkpoint metadata';const list=document.createElement('ul');list.className='compare-files';for(const path of changed){const li=document.createElement('li');li.textContent=path;list.appendChild(li)}details.append(heading,list);root.appendChild(details)}const truth=document.createElement('div');truth.className='truth';truth.textContent='Comparison reads checkpoint metadata only. Source media is neither compared nor duplicated, and either checkpoint remains restorable as a complete supported project checkpoint.';root.appendChild(truth)}
async function compareRevisions(){if(!DEEP)return status('Cross-editor checkpoint comparison requires Pro.',false);const p=document.getElementById('project').value,left=document.getElementById('compareA').value,right=document.getElementById('compareB').value;if(!p||!left||!right)return status('Choose two checkpoints first.',false);if(left===right)return status('Choose two different checkpoints to compare.',false);try{document.getElementById('compareResult').textContent='Comparing checkpoint metadata...';const data=await api('/projects/'+encodeURIComponent(p)+'/revisions/compare?left='+encodeURIComponent(left)+'&right='+encodeURIComponent(right));renderComparison(data);status('Checkpoint comparison complete · '+(data.domains_changed||[]).length+' changed domains.')}catch(e){status(e.message,false)}}
init();
</script></body></html>"""

    replacements = {
        "__PRODUCT__": escape(PRODUCT_NAME),
        "__PLAN__": escape(plan.name),
        "__LIMIT__": str(limit or "Locked"),
        "__NAME__": name,
        "__LOCKED__": locked,
        "__SNAPSHOT_DISABLED__": "" if enabled else "disabled",
        "__COMPARE_DISABLED__": "" if deep else "disabled",
        "__COMPARE_CLASS__": "" if deep else "disabled",
        "__COMPARE_NOTE__": escape(compare_note),
        "__ENABLED__": "true" if enabled else "false",
        "__DEEP__": "true" if deep else "false",
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return HTMLResponse(page)
