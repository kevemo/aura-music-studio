from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ESP Music Video Studio UI"])


@router.get("/video-studio", response_class=HTMLResponse)
def video_studio(request: Request):
    member = getattr(request.state, "member", None)
    plan = member.plan.name if member else "Member"
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP Music Video Studio</title>
<style>
body{{margin:0;background:#09000f;color:#f8f3ff;font-family:Inter,Arial,sans-serif}}
.shell{{max-width:1450px;margin:auto;padding:22px}} .top{{display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
.logo{{width:92px;height:92px;object-fit:contain;filter:drop-shadow(0 0 18px #8d2cff88)}}
h1{{margin:0;font-size:clamp(28px,4vw,50px)}} .gold{{color:#efc96d}} .muted{{color:#cbbbd8}}
.grid{{display:grid;grid-template-columns:minmax(320px,520px) 1fr;gap:18px;margin-top:20px}}
.card{{background:linear-gradient(160deg,#160420,#0d0215);border:1px solid #703a82;border-radius:18px;padding:18px;box-shadow:0 10px 35px #0008}}
label{{display:block;font-size:13px;color:#d9c9e6;margin:12px 0 5px}} select,input,textarea{{box-sizing:border-box;width:100%;background:#0a0710;color:white;border:1px solid #633776;border-radius:10px;padding:10px}}
textarea{{min-height:110px;resize:vertical}} button,.button{{border:0;border-radius:11px;padding:11px 16px;background:linear-gradient(90deg,#d49b3f,#f0d37d);color:#210713;font-weight:800;cursor:pointer;text-decoration:none;display:inline-block}}
button.secondary{{background:#35154a;color:#fff;border:1px solid #8d5aa3}} button:disabled{{opacity:.45;cursor:not-allowed}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .actions{{display:flex;gap:9px;flex-wrap:wrap;margin-top:15px}}
video{{width:100%;max-height:68vh;background:#000;border-radius:14px;border:1px solid #6b3b78}} pre{{white-space:pre-wrap;max-height:360px;overflow:auto;background:#08050a;border-radius:12px;padding:12px;color:#dccde7}}
.pill{{display:inline-block;padding:5px 9px;border-radius:999px;background:#381148;border:1px solid #77478b;margin:2px;font-size:12px}}
.lock{{opacity:.55}} .hidden{{display:none!important}} @media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><div class="shell">
<div class="top"><img class="logo" src="/brand/logo.webp" alt="ESP"><div><div class="muted">Elevate Souls Productions Presents</div><h1><span class="gold">Live Sound Studio</span> · Music Video Studio</h1><div class="muted">Aura turns the finished song into visualizers, lyric videos, beat-cut edits and optional neural scenes. Plan: <b>{plan}</b></div></div></div>
<div class="actions"><a class="button" href="/studio">← Studio</a><a class="button" href="/production-suite">Music Production</a><a class="button" href="/daw">Visual DAW</a></div>
<div class="grid">
<section class="card">
<h2>🎬 Build the video</h2>
<label>Project</label><select id="project"></select>
<label>Song / mastered audio</label><select id="audio"></select>
<label>Images / video clips</label><select id="visuals" multiple size="6"></select>
<div class="row"><div><label>Video type</label><select id="mode"><option value="visualizer">Audio-reactive visualizer</option><option value="lyric_video">Lyric video</option><option value="montage">Beat-cut montage</option></select></div><div><label>Aspect</label><select id="aspect"><option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option></select></div></div>
<div class="row"><div><label>Quality</label><select id="quality"><option value="preview">Preview</option><option value="hd" selected>HD</option><option value="4k">4K</option></select></div><div><label>FPS</label><select id="fps"><option>24</option><option selected>30</option><option>60</option></select></div></div>
<label><input id="waveform" type="checkbox" checked style="width:auto"> Audio-reactive waveform</label>
<label>Lyrics (for Lyric Video)</label><textarea id="lyrics" placeholder="[Verse 1]\nYour lyrics..."></textarea>
<label>Creative direction / storyboard idea</label><textarea id="direction">cinematic, emotional, premium music video, visuals intensify with the chorus and relax in quieter sections</textarea>
<div class="actions"><button id="render">Render Video</button><button id="storyboard" class="secondary">Aura Storyboard (Pro)</button></div>
<div id="caps" style="margin-top:14px"></div><div id="status" class="muted" style="margin-top:12px"></div>
</section>
<section class="card">
<h2>📺 Preview & Export</h2><video id="player" controls playsinline></video><div class="actions"><a id="download" class="button hidden">Download MP4</a></div><pre id="report">Select a project and render a video.</pre>
<div id="neural" class="hidden"><hr style="border-color:#52295f;margin:24px 0"><h2>✨ Neural Scene Lab · Pro</h2><div class="row"><div><label>Engine</label><select id="engine"></select></div><div><label>Seconds</label><input id="sceneSeconds" type="number" min="1" max="30" step="1" value="5"></div></div><label>Scene prompt</label><textarea id="scenePrompt">cinematic performance scene, expressive camera movement, high-end music video lighting</textarea><div class="actions"><button id="sceneBtn">Generate Neural Scene</button></div></div>
</section></div></div>
<script>
const $=id=>document.getElementById(id); let capabilities={{}};
async function json(url, options={{}}){{let r=await fetch(url,{{credentials:'same-origin',...options}}); let d=await r.json().catch(()=>({{}})); if(!r.ok) throw new Error(d.detail||JSON.stringify(d)); return d}}
async function init(){{capabilities=await json('/video/capabilities'); $('caps').innerHTML=Object.entries(capabilities.features||{{}}).map(([k,v])=>`<span class="pill ${{v?'':'lock'}}">${{v?'✓':'🔒'}} ${{k}}</span>`).join('');
$('mode').querySelector('[value=lyric_video]').disabled=!capabilities.features.lyric_video; $('mode').querySelector('[value=montage]').disabled=!capabilities.features.audio_reactive; $('quality').querySelector('[value="4k"]').disabled=!capabilities.features['4k'];
$('storyboard').disabled=!capabilities.features.storyboard; if(capabilities.features.neural_video){{$('neural').classList.remove('hidden'); $('engine').innerHTML=(capabilities.neural_engines||[]).map(x=>`<option value="${{x.id}}" ${{x.configured?'':'disabled'}}>${{x.name}} ${{x.configured?'ready':'not installed'}}</option>`).join('')}}
let projects=await json('/projects'); $('project').innerHTML=projects.map(p=>`<option value="${{p.name}}">${{p.name}}</option>`).join(''); if(projects.length) await loadAssets()}}
async function loadAssets(){{let p=$('project').value;if(!p)return;let a=await json(`/projects/${{encodeURIComponent(p)}}/assets`);let aud=a.filter(x=>x.kind==='audio');let vis=a.filter(x=>x.kind==='video'||/\.(png|jpe?g|webp)$/i.test(x.name));$('audio').innerHTML=aud.map(x=>`<option value="${{x.id}}">${{x.name}}</option>`).join('');$('visuals').innerHTML=vis.map(x=>`<option value="${{x.id}}">${{x.name}}</option>`).join('')}}
$('project').addEventListener('change',loadAssets);
$('render').onclick=async()=>{{try{{$('status').textContent='Aura is rendering the music video…';let p=$('project').value;let body={{audio_asset_id:$('audio').value,visual_asset_ids:[...$('visuals').selectedOptions].map(x=>x.value),mode:$('mode').value,lyrics:$('lyrics').value,creative_direction:$('direction').value,aspect:$('aspect').value,fps:+$('fps').value,include_waveform:$('waveform').checked,quality:$('quality').value}};let d=await json(`/video/projects/${{encodeURIComponent(p)}}/render`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});$('player').src=d.stream_url+'?v='+Date.now();$('report').textContent=JSON.stringify(d,null,2);$('status').textContent='Video render complete.';if(d.download_unlocked){{$('download').href=`/projects/${{encodeURIComponent(p)}}/outputs/file/${{d.output.split('/').map(encodeURIComponent).join('/')}}`;$('download').classList.remove('hidden')}}else $('download').classList.add('hidden')}}catch(e){{$('status').textContent=e.message}}}};
$('storyboard').onclick=async()=>{{try{{let p=$('project').value;let d=await json(`/video/projects/${{encodeURIComponent(p)}}/storyboard`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{audio_asset_id:$('audio').value,creative_direction:$('direction').value,scene_beats:16}})}});$('report').textContent=JSON.stringify(d,null,2)}}catch(e){{$('status').textContent=e.message}}}};
$('sceneBtn').onclick=async()=>{{try{{let p=$('project').value;let image=[...$('visuals').selectedOptions][0]?.value||null;let d=await json(`/video/projects/${{encodeURIComponent(p)}}/neural-scene`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{engine:$('engine').value,prompt:$('scenePrompt').value,image_asset_id:image,audio_asset_id:null,duration_seconds:+$('sceneSeconds').value,aspect:$('aspect').value,fps:24}})}});$('player').src=d.stream_url+'?v='+Date.now();$('report').textContent=JSON.stringify(d,null,2)}}catch(e){{$('status').textContent=e.message}}}};
init().catch(e=>$('status').textContent=e.message);
</script></body></html>""")
