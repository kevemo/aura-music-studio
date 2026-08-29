from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Auto Cue Prompter"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/live-overlay-studio/prompter", response_class=HTMLResponse, include_in_schema=False)
def live_auto_cue_prompter(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    html = """<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='referrer' content='no-referrer'>
<meta name='robots' content='noindex,nofollow'>
<title>Aura Auto Cue Prompter — __PRODUCT__</title>
<style>
:root{--bg:#060710;--panel:#111526;--text:#fff;--muted:#b8c1d4;--accent:#efc96b;--line:#ffffff1f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:28px 0 50px}h1{font-size:clamp(2.3rem,5vw,4.5rem);margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:1fr .8fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:18px}textarea{width:100%;min-height:420px;resize:vertical;background:#080b15;color:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;font:500 1rem/1.6 system-ui}button,input{font:inherit;border-radius:10px;border:1px solid var(--line);background:#ffffff0d;color:#fff;padding:10px 12px}button{cursor:pointer;font-weight:850}.primary{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}label{font-weight:750}input[type=range]{width:100%}.privacy{border-left:4px solid var(--accent);padding-left:12px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body><main class='wrap'>
<div style='color:var(--accent);font-weight:900'>Aura LIVE Overlay Studio · Creator-only tool</div>
<h1>Auto Cue Prompter</h1>
<p class='muted'>Welcome __DISPLAY_NAME__. Paste your LIVE show script below, then open the private prompter window. The script remains inside this browser session and is never inserted into the public overlay source or LIVE event feed.</p>
<section class='grid'>
<div class='card'>
<h2>Show script</h2>
<textarea id='script' autocomplete='off' spellcheck='true' placeholder='Paste or write your LIVE show script here…'></textarea>
<div class='row' style='margin-top:12px'><button class='primary' id='open'>Open private prompter</button><button id='clear'>Clear script</button><button id='sample'>Load sample</button></div>
<p class='muted privacy'><b>Private by design:</b> script text is not submitted to the server, database, overlay browser-source URL, event simulator or TikTok connector.</p>
</div>
<div class='card'>
<h2>Delivery controls</h2>
<label>Auto-scroll speed <span id='speedLabel'>45</span> px/sec</label><input id='speed' type='range' min='10' max='180' value='45'>
<label>Text size <span id='sizeLabel'>54</span> px</label><input id='size' type='range' min='28' max='96' value='54'>
<label>Line spacing <span id='lineLabel'>1.45</span></label><input id='line' type='range' min='110' max='220' value='145'>
<div class='row' style='margin-top:14px'><label><input id='mirror' type='checkbox'> Mirror text</label><label><input id='countdown' type='checkbox' checked> 3-second countdown</label></div>
<h3>Prompter shortcuts</h3><p class='muted'><b>Space</b> play/pause · <b>↑/↓</b> adjust speed · <b>←/→</b> move script · <b>Home</b> restart · <b>F</b> fullscreen.</p>
<p class='muted'>You can move the popup to a second monitor or position it close to your camera for more natural eye contact.</p>
</div>
</section></main>
<script>
const scriptEl=document.getElementById('script'),speed=document.getElementById('speed'),size=document.getElementById('size'),line=document.getElementById('line'),mirror=document.getElementById('mirror'),countdown=document.getElementById('countdown');
const sync=()=>{document.getElementById('speedLabel').textContent=speed.value;document.getElementById('sizeLabel').textContent=size.value;document.getElementById('lineLabel').textContent=(Number(line.value)/100).toFixed(2)};[speed,size,line].forEach(x=>x.addEventListener('input',sync));sync();
document.getElementById('clear').onclick=()=>{scriptEl.value='';scriptEl.focus()};
document.getElementById('sample').onclick=()=>{scriptEl.value=`WELCOME\n\nHello everyone and welcome to the LIVE!\n\nINTRODUCTION\nTell viewers what today's show is about and why they should stay.\n\nENGAGEMENT CUE\nAsk the audience a question and give them time to answer.\n\nMAIN SEGMENT\nDeliver your main talking points here.\n\nCALL TO ACTION\nInvite viewers to follow, share, subscribe or join the next segment.\n\nCLOSE\nThank everyone for spending their time with you.`};
function popupHtml(){const countdownScript=countdown.checked?"playing=false;count.style.display='grid';let n=3;count.textContent=n;const timer=setInterval(()=>{n--;if(n<=0){clearInterval(timer);count.style.display='none';playing=true;state()}else{count.textContent=n}},1000);":"";return `<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Aura Private Prompter</title><style>*{box-sizing:border-box}html,body{margin:0;background:#030307;color:white;height:100%;overflow:hidden;font-family:Arial,sans-serif}#top{position:fixed;z-index:5;top:0;left:0;right:0;display:flex;gap:8px;align-items:center;padding:8px;background:#090a12e8;border-bottom:1px solid #ffffff20}button{background:#ffffff12;color:white;border:1px solid #ffffff28;border-radius:8px;padding:8px 11px;font-weight:700}#stage{height:100vh;overflow-y:auto;scrollbar-width:none;padding:22vh 8vw 50vh}#stage::-webkit-scrollbar{display:none}#text{white-space:pre-wrap;font-size:${size.value}px;line-height:${Number(line.value)/100};font-weight:700;max-width:1100px;margin:auto}#guide{position:fixed;left:0;right:0;top:46%;height:2px;background:#efc96b88;pointer-events:none}#count{position:fixed;inset:0;display:none;place-items:center;background:#030307;z-index:20;font-size:20vw;font-weight:900}.mirrored #text{transform:scaleX(-1)}#status{margin-left:auto;color:#efc96b;font-weight:800}</style></head><body class="${mirror.checked?'mirrored':''}"><div id="top"><button id="toggle">Pause</button><button id="restart">Restart</button><button id="slower">Slower</button><button id="faster">Faster</button><button id="full">Fullscreen</button><span id="status">${speed.value} px/sec</span></div><div id="guide"></div><div id="stage"><div id="text"></div></div><div id="count"></div><script>const stage=document.getElementById('stage'),txt=document.getElementById('text'),status=document.getElementById('status'),count=document.getElementById('count');txt.textContent=${JSON.stringify(scriptEl.value)};let playing=true,velocity=${Number(speed.value)},last=performance.now();function frame(now){if(playing)stage.scrollTop+=velocity*((now-last)/1000);last=now;requestAnimationFrame(frame)}requestAnimationFrame(frame);function state(){document.getElementById('toggle').textContent=playing?'Pause':'Play';status.textContent=Math.round(velocity)+' px/sec'}document.getElementById('toggle').onclick=()=>{playing=!playing;state()};document.getElementById('restart').onclick=()=>stage.scrollTo({top:0,behavior:'smooth'});document.getElementById('slower').onclick=()=>{velocity=Math.max(5,velocity-5);state()};document.getElementById('faster').onclick=()=>{velocity=Math.min(250,velocity+5);state()};document.getElementById('full').onclick=()=>document.documentElement.requestFullscreen?.();addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();playing=!playing;state()}else if(e.key==='ArrowUp'){velocity=Math.min(250,velocity+5);state()}else if(e.key==='ArrowDown'){velocity=Math.max(5,velocity-5);state()}else if(e.key==='ArrowRight')stage.scrollBy({top:180,behavior:'smooth'});else if(e.key==='ArrowLeft')stage.scrollBy({top:-180,behavior:'smooth'});else if(e.key==='Home')stage.scrollTo({top:0,behavior:'smooth'});else if(e.key.toLowerCase()==='f')document.documentElement.requestFullscreen?.()});${countdownScript}state();<\/script></body></html>`}
document.getElementById('open').onclick=()=>{if(!scriptEl.value.trim()){alert('Add your show script first.');return}const win=window.open('','aura_live_private_prompter','popup,width=900,height=800,resizable=yes,scrollbars=no');if(!win){alert('Allow pop-ups for the Command Center to open the private prompter.');return}win.document.open();win.document.write(popupHtml());win.document.close();win.focus()};
</script></body></html>"""
    html = html.replace("__PRODUCT__", product).replace("__DISPLAY_NAME__", display_name)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = ["router", "live_auto_cue_prompter"]
