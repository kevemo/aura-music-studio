from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .branding import PRODUCT_FULL_NAME, TAGLINE

router = APIRouter()


@router.get("/aura", response_class=HTMLResponse)
def aura_control_room(request: Request):
    member = getattr(request.state, "member", None)
    name = escape(member.user.get("display_name") if member else "Member")
    plan = escape(member.plan.name if member else "Member")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Spoken Aura — {escape(PRODUCT_FULL_NAME)}</title><style>
:root{{--bg:#09060e;--panel:#18101f;--line:#422b51;--gold:#e9bd63;--muted:#cabfd2}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top,#301444,#09060e 55%);color:white;font-family:Inter,system-ui,sans-serif}}.wrap{{max-width:920px;margin:auto;padding:25px}}.card{{background:#160f1e;border:1px solid var(--line);border-radius:24px;padding:25px;margin:16px 0}}h1{{font-size:clamp(2.3rem,7vw,4.5rem);margin:.2em 0}}.gold{{color:var(--gold)}}.muted{{color:var(--muted)}}button,.btn,select{{font:inherit;border:1px solid var(--line);background:#251630;color:white;border-radius:12px;padding:12px 16px;font-weight:800}}button{{cursor:pointer}}.primary{{background:linear-gradient(135deg,#f0ce78,#bd8b32);color:#170e1d;border:0}}.recording{{background:#6d2035!important}}.orb{{width:180px;height:180px;border-radius:50%;margin:28px auto;background:radial-gradient(circle at 35% 30%,#f6d98d,#9a4fd2 45%,#261034 75%);box-shadow:0 0 80px #9b4bd466;display:grid;place-items:center;font-size:3rem}}.status{{white-space:pre-wrap;background:#0b0710;border:1px solid #31213c;border-radius:14px;padding:15px;min-height:100px}}.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}.row>*{{flex:1}}audio{{width:100%;margin-top:14px}}
</style></head><body><div class='wrap'><div class='row'><div><div class='gold'><b>ESP LIVE SOUND STUDIO</b></div><div class='muted'>Spoken Aura · {plan}</div></div><a class='btn' href='/studio'>Back to Studio</a></div>
<div class='card' style='text-align:center'><h1>Talk to <span class='gold'>Aura</span></h1><p class='muted'>Hello {name}. Speak naturally: “make the chorus bigger”, “split this track”, “clean the vocal”, “master this”, or describe the music you want to create.</p><div class='orb'>✦</div><div class='row'><select id='project'><option value=''>No project selected</option></select><button id='record' class='primary'>Start listening</button></div><p id='micState' class='muted'>Microphone idle.</p></div>
<div class='card'><h3>What Aura heard</h3><div id='transcript' class='status'></div><h3>Aura's plan</h3><div id='plan' class='status'></div><h3>Aura says</h3><div id='reply' class='status'></div><audio id='voice' controls></audio></div></div>
<script>
let recorder=null, chunks=[], stream=null;
async function loadProjects(){{try{{const r=await fetch('/projects',{{credentials:'same-origin'}});if(!r.ok)return;const items=await r.json();const s=document.getElementById('project');for(const p of items){{const o=document.createElement('option');o.value=p.name;o.textContent=p.name;s.appendChild(o)}}}}catch(e){{}}}}
async function speak(text){{try{{const r=await fetch('/speech/synthesize',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{text}})}});if(!r.ok)return;const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.getElementById('voice');a.src=url;await a.play().catch(()=>{{}})}}catch(e){{}}}}
async function send(blob){{const f=new FormData();f.append('audio',blob,'aura-command.webm');const p=document.getElementById('project').value;if(p)f.append('project_name',p);f.append('speak_reply','false');document.getElementById('micState').textContent='Aura is listening to what you said...';try{{const r=await fetch('/speech/command',{{method:'POST',credentials:'same-origin',body:f}});const data=await r.json();if(!r.ok)throw new Error(data.detail||JSON.stringify(data));document.getElementById('transcript').textContent=data.transcript||'';document.getElementById('plan').textContent=JSON.stringify(data.plan,null,2);document.getElementById('reply').textContent=data.spoken_text||'';document.getElementById('micState').textContent='Ready.';if(data.spoken_text)speak(data.spoken_text)}}catch(e){{document.getElementById('micState').textContent='Aura could not process that: '+e.message}}}}
document.getElementById('record').onclick=async()=>{{const b=document.getElementById('record');if(recorder&&recorder.state==='recording'){{recorder.stop();b.textContent='Start listening';b.classList.remove('recording');document.getElementById('micState').textContent='Processing...';return}}try{{stream=await navigator.mediaDevices.getUserMedia({{audio:true}});chunks=[];recorder=new MediaRecorder(stream);recorder.ondataavailable=e=>{{if(e.data.size)chunks.push(e.data)}};recorder.onstop=()=>{{stream.getTracks().forEach(t=>t.stop());send(new Blob(chunks,{{type:recorder.mimeType||'audio/webm'}}))}};recorder.start();b.textContent='Stop & send';b.classList.add('recording');document.getElementById('micState').textContent='Listening...'}}catch(e){{document.getElementById('micState').textContent='Microphone unavailable: '+e.message}}}};
loadProjects();
</script></body></html>""")
