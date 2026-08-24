from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

router = APIRouter(include_in_schema=False)

TASKS_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  function q(id){return document.getElementById(id)}
  function esc(v){try{return window.esc(v)}catch(_){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}}
  async function request(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function when(value){if(!value)return '—';try{return new Date(value).toLocaleString()}catch(_){return value}}
  function intervalLabel(minutes){if(!minutes)return 'One time';if(minutes%10080===0)return `Every ${minutes/10080} week${minutes===10080?'':'s'}`;if(minutes%1440===0)return `Every ${minutes/1440} day${minutes===1440?'':'s'}`;if(minutes%60===0)return `Every ${minutes/60} hour${minutes===60?'':'s'}`;return `Every ${minutes} minutes`}

  function ensureDrawer(){
    if(q('auraTasksDrawer'))return q('auraTasksDrawer');
    const drawer=document.createElement('div');drawer.id='auraTasksDrawer';drawer.className='drawer';
    drawer.innerHTML=`
      <div style="display:flex;align-items:center;gap:8px"><div style="flex:1"><b>Aura Tasks</b><div class="muted" style="font-size:.72rem">Durable reminders & read-only scheduled research</div></div><button class="btn" id="auraTasksClose">✕</button></div>
      <div id="auraTasksWorker" style="margin:12px 0;padding:10px;border:1px solid #ffffff18;border-radius:12px"></div>
      <div style="display:grid;gap:8px;padding:10px;border:1px solid #ffffff18;border-radius:12px;background:#ffffff04">
        <input id="auraTaskTitle" class="search" style="margin:0" placeholder="Task title">
        <select id="auraTaskKind" class="select"><option value="reminder">Reminder</option><option value="prompt">Aura follow-up</option><option value="research">Scheduled research</option></select>
        <textarea id="auraTaskPrompt" class="search" style="margin:0;min-height:92px;resize:vertical" placeholder="What should Aura remind/research/follow up on?"></textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div><label class="muted" style="font-size:.68rem">Run after</label><select id="auraTaskDelay" class="select"><option value="60">1 hour</option><option value="180">3 hours</option><option value="360">6 hours</option><option value="720">12 hours</option><option value="1440">1 day</option><option value="10080">1 week</option><option value="custom">Specific time</option></select></div>
          <div><label class="muted" style="font-size:.68rem">Repeat</label><select id="auraTaskRepeat" class="select"><option value="">One time</option><option value="60">Hourly</option><option value="360">Every 6 hours</option><option value="720">Every 12 hours</option><option value="1440">Daily</option><option value="10080">Weekly</option></select></div>
        </div>
        <input id="auraTaskRunAt" type="datetime-local" class="search" style="display:none;margin:0">
        <button id="auraTaskCreate" class="btn primary">＋ Create Aura Task</button>
        <div class="muted" style="font-size:.65rem;line-height:1.45">Background tasks are read-only. They cannot edit projects, publish social posts, run code, clone voices or perform other high-impact actions.</div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:14px 0 6px"><b>Scheduled</b><button id="auraTasksRefresh" class="mini">Refresh</button></div>
      <div id="auraTasksRows"></div>`;
    document.body.append(drawer);
    q('auraTasksClose').onclick=()=>drawer.classList.remove('open');
    q('auraTasksRefresh').onclick=loadTasks;
    q('auraTaskDelay').onchange=()=>{q('auraTaskRunAt').style.display=q('auraTaskDelay').value==='custom'?'block':'none'};
    q('auraTaskCreate').onclick=createTask;
    return drawer;
  }

  function workerHTML(worker){
    const ready=!!worker?.ready;const rows=worker?.workers||[];const last=rows[0];
    return `<div style="display:flex;align-items:center;gap:8px"><span style="width:9px;height:9px;border-radius:50%;background:${ready?'#73e2aa':'#ffb36b'};box-shadow:0 0 14px ${ready?'#73e2aa77':'#ffb36b55'}"></span><b>${ready?'Task worker online':'Task worker offline'}</b></div><div class="muted" style="font-size:.67rem;margin-top:5px">${ready&&last?`Last heartbeat ${esc(when(last.heartbeat_at))}`:'Tasks can be saved now, but they will not execute until the worker process is running.'}</div>`;
  }

  function rowHTML(t){
    const state=t.status||'active';const enabled=!!t.enabled;
    return `<div class="mem" data-task="${esc(t.id)}"><div style="display:flex;gap:8px;align-items:flex-start"><div style="flex:1"><b>${esc(t.title)}</b><div class="muted" style="font-size:.67rem;text-transform:capitalize">${esc(t.kind)} · ${esc(state)} · ${esc(intervalLabel(t.interval_minutes))}</div></div><span style="font-size:.68rem;color:${enabled?'#73e2aa':'#a9b2c8'}">${enabled?'ACTIVE':'PAUSED'}</span></div><p style="font-size:.76rem;line-height:1.45;white-space:pre-wrap">${esc(t.prompt)}</p><div class="muted" style="font-size:.67rem">Next: ${esc(when(t.next_run_at))}${t.last_run_at?` · Last: ${esc(when(t.last_run_at))}`:''}</div>${t.last_error?`<div style="color:#ff8fa6;font-size:.67rem;margin-top:5px">${esc(t.last_error)}</div>`:''}<div style="display:flex;gap:5px;margin-top:7px"><button class="mini" data-task-toggle="${esc(t.id)}" data-enabled="${enabled?'1':'0'}">${enabled?'Pause':'Resume'}</button><button class="mini" data-task-delete="${esc(t.id)}">Delete</button></div></div>`;
  }

  async function loadTasks(){
    try{const data=await request(`${api}/tasks`);q('auraTasksWorker').innerHTML=workerHTML(data.worker||{});q('auraTasksRows').innerHTML=(data.tasks||[]).map(rowHTML).join('')||'<p class="muted">No Aura Tasks yet.</p>';bindRows()}catch(error){toast(error.message,true)}
  }
  function bindRows(){
    document.querySelectorAll('[data-task-toggle]').forEach(b=>b.onclick=async()=>{try{const enabled=b.dataset.enabled!=='1';await request(`${api}/tasks/${encodeURIComponent(b.dataset.taskToggle)}`,{method:'PATCH',body:JSON.stringify({enabled})});await loadTasks()}catch(e){toast(e.message,true)}});
    document.querySelectorAll('[data-task-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this Aura Task?'))return;try{await request(`${api}/tasks/${encodeURIComponent(b.dataset.taskDelete)}`,{method:'DELETE'});await loadTasks()}catch(e){toast(e.message,true)}});
  }
  async function createTask(){
    try{
      if(typeof current==='undefined'||!current)throw new Error('Open an Aura conversation first.');
      const title=q('auraTaskTitle').value.trim(),prompt=q('auraTaskPrompt').value.trim();if(!title||!prompt)throw new Error('Enter a title and task instruction.');
      const delay=q('auraTaskDelay').value,repeat=q('auraTaskRepeat').value;const body={title,kind:q('auraTaskKind').value,prompt,interval_minutes:repeat?Number(repeat):null};
      if(delay==='custom'){const raw=q('auraTaskRunAt').value;if(!raw)throw new Error('Choose the run time.');body.run_at=new Date(raw).toISOString()}else body.delay_minutes=Number(delay);
      const task=await request(`${api}/threads/${encodeURIComponent(current)}/tasks`,{method:'POST',body:JSON.stringify(body)});q('auraTaskTitle').value='';q('auraTaskPrompt').value='';toast(`Aura Task scheduled: ${task.title}`);await loadTasks();
    }catch(error){toast(error.message,true)}
  }

  const foot=document.querySelector('.sideFoot');if(foot&&!q('auraTasksButton')){const b=document.createElement('button');b.id='auraTasksButton';b.className='btn';b.textContent='⏰ Aura Tasks';b.onclick=async()=>{ensureDrawer().classList.add('open');await loadTasks()};foot.prepend(b)}
})();
"""


@router.get('/aura-intelligence/tasks-ui.js')
def tasks_ui_script():
    return Response(content=TASKS_SCRIPT, media_type='application/javascript', headers={'Cache-Control':'no-store'})


class AuraTasksUIMiddleware(BaseHTTPMiddleware):
    """Inject Aura Tasks only into the signed-in Aura HTML workspace."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path != '/aura-intelligence' or request.method.upper() != 'GET':
            return response
        content_type = (response.headers.get('content-type') or '').lower()
        if not content_type.startswith('text/html'):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        marker = "<script src='/aura-intelligence/tasks-ui.js'></script>"
        if marker not in text:
            text = text.replace('</body>', marker + '</body>')
        encoded = text.encode('utf-8')
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key,value) for key,value in response.raw_headers if key.lower()!=b'content-length']
        raw_headers.append((b'content-length', str(len(encoded)).encode('ascii')))
        migrated.raw_headers = raw_headers
        return migrated


__all__=['router','AuraTasksUIMiddleware']
