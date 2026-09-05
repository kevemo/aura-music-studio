from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Support Casework Portal"])

CSS = """
:root{color-scheme:dark;--bg:#08050e;--panel:#16101f;--panel2:#21152e;--line:#483653;--gold:#e9c169;--text:#fff;--muted:#c9bdd2;--ok:#75dba0;--bad:#ff91a5}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#351449 0,transparent 34%),var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}.wrap{width:min(1220px,calc(100% - 28px));margin:auto;padding:28px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.card{background:linear-gradient(145deg,var(--panel),#110a18);border:1px solid var(--line);border-radius:17px;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.muted{color:var(--muted);line-height:1.5}.btn,button{border:0;border-radius:10px;padding:10px 13px;background:var(--gold);color:#1c1024;text-decoration:none;font-weight:850;cursor:pointer}.secondary{background:#382646;color:#fff}.danger{background:#5b2639;color:#fff}input,select,textarea{width:100%;padding:11px;border:1px solid var(--line);border-radius:10px;background:#0b0711;color:#fff;margin:5px 0 10px}textarea{min-height:95px;resize:vertical}label{font-weight:800}.pill{display:inline-block;border:1px solid var(--line);padding:4px 8px;border-radius:999px;font-size:.74rem}.msg{border-left:3px solid var(--gold);padding:10px 12px;margin:8px 0;background:#ffffff08}.msg.internal{border-left-color:#b98cff}.notice{border-left:4px solid var(--gold)}.error{border-left-color:var(--bad)}.ok{border-left-color:var(--ok)}.hidden{display:none}code{word-break:break-all}a{color:#f4d68d}:focus-visible{outline:3px solid #fff;outline-offset:3px}@media(max-width:760px){.grid,.three{grid-template-columns:1fr}}
"""


def _page(role: str) -> HTMLResponse:
    staff = role in {"agent", "both", "owner"}
    owner = role == "owner"
    staff_controls = ""
    if staff:
        staff_controls = """
        <section class='card' id='staffTools'>
          <h2>Staff case controls</h2>
          <div class='grid three'>
            <div><label>Related / duplicate case ID</label><input id='relatedCaseId' maxlength='64'><label>Relation</label><select id='relationType'><option value='related'>Related</option><option value='duplicate_of'>Duplicate of</option><option value='supersedes'>Supersedes</option></select><button id='linkCase'>Add case relation</button></div>
            <div><label>Related record type</label><input id='resourceType' maxlength='80' placeholder='creator_review'><label>Record ID</label><input id='resourceId' maxlength='180'><button id='linkRecord'>Link related record</button></div>
            <div><label>Assignee user ID</label><input id='assigneeId' maxlength='128'><label>Reason</label><input id='assignReason' maxlength='1000'><button id='assignCase'>Record assignment</button></div>
          </div>
          <div class='grid'>
            <div><label>Escalation target</label><select id='escalationTarget'><option>technical</option><option>compliance</option><option>safety</option><option>owner</option><option>shop</option><option>social</option></select></div>
            <div><label>Escalation reason</label><input id='escalationReason' maxlength='3000'></div>
          </div><button id='escalateCase'>Record escalation history</button>
        </section>
        """
    role_note = "Owner queue + staff controls" if owner else "Assigned Creator queue + staff controls" if staff else "Your private Creator support cases"
    body = f"""
    <div class='top'><div><div class='muted'>ESP Support &amp; Safety · {escape(role_note)}</div><h1>Support Casework Desk</h1><p class='muted'>One case record, explicit Creator-visible replies, private staff notes, protected attachments and auditable relationship/history controls.</p></div><div><a class='btn secondary' href='/command-center/support'>Support Center</a> <a class='btn secondary' href='/command-center/level-up'>Level Up</a></div></div>
    <section id='status' class='card notice hidden' role='status' aria-live='polite'></section>
    <section class='card'><div class='row'><div><h2>Cases</h2><p class='muted'>Select a case. Internal items are never requested by Creator views.</p></div><button id='reload' class='secondary'>Reload</button></div><select id='caseSelect'><option value=''>Choose a support case</option></select></section>
    <section id='caseCard' class='card hidden'><div class='row'><div><span id='casePill' class='pill'></span><h2 id='caseSubject'></h2><p id='caseDescription' class='muted'></p></div><div><span class='pill'>Revision <b id='revision'>0</b></span></div></div><p id='caseMeta' class='muted'></p></section>
    <section id='conversationCard' class='card hidden'><h2>Conversation</h2><div id='messages'></div><label>Reply / note<textarea id='messageBody' maxlength='6000'></textarea></label><label>Visibility<select id='messageVisibility'><option value='creator'>Creator-visible reply</option>{"<option value='internal'>Internal staff note</option>" if staff else ""}</select></label><button id='sendMessage'>Add message</button></section>
    <section id='attachmentCard' class='card hidden'><h2>Attachments</h2><div id='attachments'></div><form id='attachmentForm'><label>Evidence file<input id='attachmentFile' type='file' required accept='.pdf,.png,.jpg,.jpeg,.webp,.txt,.csv,.json,.xlsx'></label><label>Visibility<select id='attachmentVisibility'><option value='creator'>Creator-visible</option>{"<option value='internal'>Internal staff only</option>" if staff else ""}</select></label><button type='submit'>Upload protected attachment</button></form><p class='muted'>Maximum 10 MB. Local filesystem paths are never returned to this page.</p></section>
    <section id='relationsCard' class='card hidden'><h2>Related records &amp; history</h2><div id='relations'></div><div id='events'></div></section>
    {staff_controls}
    <script>
    const ROLE={role!r}, STAFF={str(staff).lower()}, OWNER={str(owner).lower()};
    const api='/command-center/api/support'; let current=null;
    const $=id=>document.getElementById(id);
    function key(prefix){{return `${{prefix}}-${{crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random()}}`;}}
    function status(message,ok=true){{const el=$('status');el.textContent=message;el.classList.remove('hidden','ok','error');el.classList.add(ok?'ok':'error');}}
    async function req(url,opt={{}}){{const r=await fetch(url,{{credentials:'same-origin',...opt}});let d={{}};try{{d=await r.json();}}catch(_e){{}}if(!r.ok){{const detail=d.detail;throw new Error(typeof detail==='string'?detail:(detail?.code?`${{detail.code}} (current revision ${{detail.current_revision}})`:JSON.stringify(detail||{{status:r.status}})));}}return d;}}
    async function loadCases(){{
      try{{let rows=[];if(OWNER){{rows=(await req(api+'/owner/cases')).cases||[];}}else if(STAFF){{const [own,assigned]=await Promise.all([req(api+'/cases'),req('/command-center/api/agent/support/cases')]);const map=new Map();for(const x of [...(own.cases||[]),...(assigned.cases||[])])map.set(x.id,x);rows=[...map.values()];}}else{{rows=(await req(api+'/cases')).cases||[];}}
      const select=$('caseSelect');const previous=select.value;select.innerHTML='<option value="">Choose a support case</option>';for(const c of rows){{const o=document.createElement('option');o.value=c.id;o.textContent=`${{c.subject||'Support case'}} · ${{c.status||''}} · ${{c.severity||''}}`;select.appendChild(o);}}if(previous&&[...select.options].some(o=>o.value===previous)){{select.value=previous;await loadCase(previous);}}}}catch(e){{status(e.message,false);}}}}
    async function loadCase(id){{if(!id){{current=null;for(const x of ['caseCard','conversationCard','attachmentCard','relationsCard'])$(x).classList.add('hidden');return;}}try{{current=await req(`${{api}}/cases/${{encodeURIComponent(id)}}/casework`);render();}}catch(e){{status(e.message,false);}}}}
    function render(){{const c=current.case||{{}};$('casePill').textContent=`${{c.category||''}} · ${{c.severity||''}} · ${{c.status||''}}`;$('caseSubject').textContent=c.subject||'Support case';$('caseDescription').textContent=c.description||'';$('caseMeta').textContent=`Created ${{(c.created_at||'').slice(0,16)}} · View: ${{current.view}}`;$('revision').textContent=current.revision;
      $('messages').innerHTML='';for(const m of current.messages||[]){{const d=document.createElement('div');d.className='msg '+(m.visibility==='internal'?'internal':'');const b=document.createElement('b');b.textContent=m.visibility==='internal'?'Internal note':'Case reply';const p=document.createElement('p');p.textContent=m.body;const s=document.createElement('small');s.textContent=(m.created_at||'').slice(0,16);d.append(b,p,s);$('messages').appendChild(d);}}if(!(current.messages||[]).length)$('messages').innerHTML='<p class="muted">No casework messages yet.</p>';
      $('attachments').innerHTML='';for(const a of current.attachments||[]){{const p=document.createElement('p');const link=document.createElement('a');link.href=a.download_path;link.textContent=`${{a.original_name}} (${{a.visibility}})`;p.appendChild(link);$('attachments').appendChild(p);}}if(!(current.attachments||[]).length)$('attachments').innerHTML='<p class="muted">No visible attachments.</p>';
      $('relations').innerHTML='<h3>Relations</h3>';for(const r of current.relations||[]){{const p=document.createElement('p');p.className='muted';p.textContent=`${{r.relation_type}} · ${{r.related_case_id||r.resource_type+':'+r.resource_id}}${{r.label?' · '+r.label:''}}`; $('relations').appendChild(p);}}if(!(current.relations||[]).length)$('relations').insertAdjacentHTML('beforeend','<p class="muted">No visible relations.</p>');
      $('events').innerHTML='<h3>Assignment / escalation history</h3>';for(const e of current.events||[]){{const p=document.createElement('p');p.className='muted';p.textContent=`${{e.event_type}} · ${{JSON.stringify(e.details||{{}})}}`; $('events').appendChild(p);}}if(!(current.events||[]).length)$('events').insertAdjacentHTML('beforeend','<p class="muted">No visible workflow events.</p>');
      for(const x of ['caseCard','conversationCard','attachmentCard','relationsCard'])$(x).classList.remove('hidden');}}
    $('caseSelect').addEventListener('change',e=>loadCase(e.target.value));$('reload').onclick=loadCases;
    $('sendMessage').onclick=async()=>{{if(!current)return status('Choose a case first.',false);const body=$('messageBody').value.trim();if(!body)return status('Enter a reply or note.',false);try{{const d=await req(`${{api}}/cases/${{current.case.id}}/messages`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{body,visibility:$('messageVisibility').value,expected_revision:current.revision,idempotency_key:key('message')}})}});current=d.casework;$('messageBody').value='';render();status('Message recorded.',true);}}catch(e){{status(e.message,false);await loadCase(current.case.id);}}}};
    $('attachmentForm').addEventListener('submit',async e=>{{e.preventDefault();if(!current)return status('Choose a case first.',false);const file=$('attachmentFile').files[0];if(!file)return;const form=new FormData();form.append('attachment',file);form.append('visibility',$('attachmentVisibility').value);form.append('expected_revision',String(current.revision));form.append('idempotency_key',key('attachment'));try{{const d=await req(`${{api}}/cases/${{current.case.id}}/attachments`,{{method:'POST',body:form}});current=d.casework;e.target.reset();render();status('Protected attachment added.',true);}}catch(err){{status(err.message,false);await loadCase(current.case.id);}}}});
    if(STAFF){{
      $('linkCase').onclick=()=>relation({{relation_type:$('relationType').value,related_case_id:$('relatedCaseId').value.trim(),resource_type:'',resource_id:'',label:'',visibility:'internal'}});
      $('linkRecord').onclick=()=>relation({{relation_type:'related_record',related_case_id:null,resource_type:$('resourceType').value.trim(),resource_id:$('resourceId').value.trim(),label:'',visibility:'internal'}});
      async function relation(payload){{if(!current)return status('Choose a case first.',false);try{{payload.expected_revision=current.revision;payload.idempotency_key=key('relation');const d=await req(`${{api}}/cases/${{current.case.id}}/relations`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});current=d.casework;render();status('Relationship recorded.',true);}}catch(e){{status(e.message,false);await loadCase(current.case.id);}}}}
      $('assignCase').onclick=async()=>{{if(!current)return status('Choose a case first.',false);try{{const d=await req(`${{api}}/cases/${{current.case.id}}/assignment`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{assignee_user_id:$('assigneeId').value.trim(),reason:$('assignReason').value.trim(),expected_revision:current.revision,idempotency_key:key('assign')}})}});current=d.casework;render();status('Assignment history recorded.',true);}}catch(e){{status(e.message,false);await loadCase(current.case.id);}}}};
      $('escalateCase').onclick=async()=>{{if(!current)return status('Choose a case first.',false);try{{const d=await req(`${{api}}/cases/${{current.case.id}}/escalation-history`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{target:$('escalationTarget').value,reason:$('escalationReason').value.trim(),expected_revision:current.revision,idempotency_key:key('escalate')}})}});current=d.casework;render();status('Escalation history recorded.',true);}}catch(e){{status(e.message,false);await loadCase(current.case.id);}}}};
    }}
    loadCases();
    </script>
    """
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Support Casework Desk</title>"
        f"<style>{CSS}</style></head><body><main class='wrap'>{body}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/command-center/support/casework", response_class=HTMLResponse, include_in_schema=False)
def support_casework_portal(request: Request):
    _member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    return _page(role)


__all__ = ["router"]