from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(prefix="/creative", tags=["pulsar-player"])

PULSAR_PLAYER_SCRIPT = r"""
(()=>{
  if(window.PulsarPlayer)return;
  const KEY='pulsar-frequency-house-player-v1';
  const MAX_QUEUE=100;
  const cleanItem=item=>({
    id:String(item?.id||item?.src||''),
    src:String(item?.src||''),
    kind:['audio','music','video'].includes(String(item?.kind||''))?String(item.kind):'audio',
    title:String(item?.title||'Untitled creation').slice(0,180),
    project:String(item?.project||'').slice(0,180),
    artwork:String(item?.artwork||''),
    elementId:String(item?.elementId||''),
    version:String(item?.version||''),
  });
  const defaultState=()=>({queue:[],index:-1,volume:.85,muted:false,loop:false,currentTime:0,playing:false});
  function load(){try{const raw=JSON.parse(localStorage.getItem(KEY)||'{}');return {...defaultState(),...raw,queue:Array.isArray(raw.queue)?raw.queue.map(cleanItem).filter(x=>x.src).slice(0,MAX_QUEUE):[]}}catch(_){return defaultState()}}
  const state=load();
  let audio,video,root,title,meta,playBtn,seek,time,volume,loopBtn,videoShell;
  const current=()=>state.index>=0&&state.index<state.queue.length?state.queue[state.index]:null;
  const fmt=n=>{n=Number.isFinite(n)?Math.max(0,n):0;const m=Math.floor(n/60),s=Math.floor(n%60);return `${m}:${String(s).padStart(2,'0')}`};
  const save=()=>{try{localStorage.setItem(KEY,JSON.stringify({...state,currentTime:active()?.currentTime||0,playing:!!active()&&!active().paused}))}catch(_){}};
  const active=()=>current()?.kind==='video'?video:audio;
  function ensure(){
    if(root)return;
    root=document.createElement('section');root.id='pulsarPlayer';root.setAttribute('aria-label','Pulsar Player');
    root.innerHTML=`<style>
      #pulsarPlayer{position:fixed;left:12px;right:12px;bottom:12px;z-index:120;display:none;align-items:center;gap:10px;padding:10px 12px;border:1px solid #ffffff26;border-radius:17px;background:linear-gradient(145deg,#17101ff5,#090711f5);box-shadow:0 18px 70px #000b;backdrop-filter:blur(18px);color:#fff;font-family:Inter,system-ui,sans-serif}
      #pulsarPlayer button{border:1px solid #ffffff20;border-radius:10px;background:#ffffff0c;color:#fff;padding:7px 9px;font-weight:850;cursor:pointer}#pulsarPlayer button:hover{border-color:#f2c86f}.pp-info{min-width:0;width:min(260px,24vw)}.pp-title{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pp-meta{display:block;color:#bdb6c7;font-size:.68rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pp-controls{display:flex;align-items:center;gap:5px}.pp-seek{flex:1;min-width:100px}.pp-time{font-size:.68rem;color:#c8c0d2;min-width:78px;text-align:center}.pp-volume{width:90px}.pp-loop.on{border-color:#f2c86f!important;color:#f2c86f}.pp-video{position:fixed;inset:0;z-index:121;background:#020205e8;display:none;place-items:center;padding:20px}.pp-video.open{display:grid}.pp-video-inner{width:min(1100px,100%);position:relative}.pp-video video{display:block;width:100%;max-height:82vh;background:#000;border-radius:16px}.pp-close{position:absolute;right:8px;top:-44px}.pp-queue{position:fixed;right:14px;bottom:85px;width:min(420px,calc(100% - 28px));max-height:55vh;overflow:auto;z-index:121;border:1px solid #ffffff20;border-radius:15px;background:#0b0811f8;padding:10px;display:none}.pp-queue.open{display:block}.pp-row{display:flex;gap:8px;align-items:center;border-bottom:1px solid #ffffff12;padding:8px}.pp-row:last-child{border-bottom:0}.pp-row-main{flex:1;min-width:0}.pp-row b,.pp-row small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pp-row small{color:#a9a1b2}.pp-row.current{outline:1px solid #f2c86f55;border-radius:9px}@media(max-width:760px){#pulsarPlayer{left:6px;right:6px;bottom:6px;display:none;flex-wrap:wrap}.pp-info{width:calc(100% - 120px);flex:1}.pp-seek{order:5;flex-basis:100%}.pp-time{order:6}.pp-volume{display:none}}
    </style><div class="pp-info"><b class="pp-title">Pulsar Player</b><small class="pp-meta">No creation selected</small></div><div class="pp-controls"><button data-pp="prev" title="Previous">⏮</button><button data-pp="play" title="Play / pause">▶</button><button data-pp="next" title="Next">⏭</button></div><input class="pp-seek" type="range" min="0" max="1000" value="0" aria-label="Seek"><span class="pp-time">0:00 / 0:00</span><input class="pp-volume" type="range" min="0" max="1" step="0.01" value=".85" aria-label="Volume"><button class="pp-loop" data-pp="loop" title="Loop">↻</button><button data-pp="video" title="Open video">▣</button><button data-pp="queue" title="Queue">☷</button><audio preload="metadata"></audio>`;
    videoShell=document.createElement('div');videoShell.className='pp-video';videoShell.innerHTML=`<div class="pp-video-inner"><button class="pp-close">✕ Close</button><video playsinline controls preload="metadata"></video></div>`;
    const queueShell=document.createElement('div');queueShell.className='pp-queue';queueShell.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center"><b>Play Queue</b><button data-pp-clear>Clear</button></div><div data-pp-rows></div>';
    document.body.append(root,videoShell,queueShell);
    audio=root.querySelector('audio');video=videoShell.querySelector('video');title=root.querySelector('.pp-title');meta=root.querySelector('.pp-meta');playBtn=root.querySelector('[data-pp="play"]');seek=root.querySelector('.pp-seek');time=root.querySelector('.pp-time');volume=root.querySelector('.pp-volume');loopBtn=root.querySelector('.pp-loop');
    volume.value=String(state.volume);audio.volume=video.volume=state.volume;audio.muted=video.muted=state.muted;loopBtn.classList.toggle('on',state.loop);
    root.addEventListener('click',e=>{const b=e.target.closest('[data-pp]');if(!b)return;const a=b.dataset.pp;if(a==='play')toggle();else if(a==='prev')previous();else if(a==='next')next();else if(a==='loop'){state.loop=!state.loop;loopBtn.classList.toggle('on',state.loop);save()}else if(a==='video')openVideo();else if(a==='queue'){queueShell.classList.toggle('open');renderQueue()}});
    videoShell.querySelector('.pp-close').onclick=()=>videoShell.classList.remove('open');
    queueShell.querySelector('[data-pp-clear]').onclick=()=>clear();
    queueShell.addEventListener('click',e=>{const b=e.target.closest('[data-pp-index]');if(b)playIndex(Number(b.dataset.ppIndex))});
    seek.addEventListener('input',()=>{const m=active();if(m&&Number.isFinite(m.duration)&&m.duration>0)m.currentTime=(Number(seek.value)/1000)*m.duration});
    volume.addEventListener('input',()=>{state.volume=Number(volume.value);audio.volume=video.volume=state.volume;save()});
    [audio,video].forEach(m=>{m.addEventListener('timeupdate',sync);m.addEventListener('durationchange',sync);m.addEventListener('play',()=>{state.playing=true;playBtn.textContent='⏸';save()});m.addEventListener('pause',()=>{state.playing=false;playBtn.textContent='▶';save()});m.addEventListener('ended',()=>{if(state.loop){m.currentTime=0;m.play().catch(()=>{})}else next()})});
    document.addEventListener('click',e=>{const b=e.target.closest('[data-pulsar-play]');if(!b)return;e.preventDefault();play({id:b.dataset.pulsarId||b.dataset.pulsarPlay,src:b.dataset.pulsarPlay,kind:b.dataset.pulsarKind||'audio',title:b.dataset.pulsarTitle||'Creation',project:b.dataset.pulsarProject||'',elementId:b.dataset.pulsarElement||''})});
    restore();
  }
  function renderQueue(){const box=document.querySelector('[data-pp-rows]');if(!box)return;box.innerHTML=state.queue.length?state.queue.map((x,i)=>`<div class="pp-row ${i===state.index?'current':''}"><div class="pp-row-main"><b>${escapeHtml(x.title)}</b><small>${escapeHtml(x.project||x.kind)}</small></div><button data-pp-index="${i}">Play</button></div>`).join(''):'<p style="color:#aaa">Queue is empty.</p>'}
  const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function setSource(item,timeAt=0,autoplay=false){ensure();audio.pause();video.pause();const m=item.kind==='video'?video:audio;m.src=item.src;m.volume=state.volume;m.muted=state.muted;m.loop=false;title.textContent=item.title;meta.textContent=[item.project,item.kind,item.version].filter(Boolean).join(' · ')||item.kind;root.style.display='flex';root.querySelector('[data-pp="video"]').style.display=item.kind==='video'?'inline-block':'none';m.addEventListener('loadedmetadata',()=>{if(timeAt>0&&Number.isFinite(m.duration))m.currentTime=Math.min(timeAt,Math.max(0,m.duration-.1));sync()},{once:true});if(autoplay)m.play().catch(()=>{});renderQueue()}
  function restore(){const item=current();if(!item)return;setSource(item,Number(state.currentTime)||0,false)}
  function play(item,{append=true}={}){ensure();const clean=cleanItem(item);if(!clean.src)return;let idx=state.queue.findIndex(x=>x.id===clean.id&&x.src===clean.src);if(idx<0){if(!append)state.queue=[];state.queue.push(clean);if(state.queue.length>MAX_QUEUE)state.queue=state.queue.slice(-MAX_QUEUE);idx=state.queue.length-1}else state.queue[idx]={...state.queue[idx],...clean};state.index=idx;state.currentTime=0;setSource(state.queue[idx],0,true);save()}
  function enqueue(items){ensure();for(const item of (Array.isArray(items)?items:[items])){const clean=cleanItem(item);if(clean.src&&!state.queue.some(x=>x.id===clean.id&&x.src===clean.src))state.queue.push(clean)}state.queue=state.queue.slice(-MAX_QUEUE);if(state.index<0&&state.queue.length)state.index=0;renderQueue();save();return [...state.queue]}
  function playIndex(index){ensure();if(index<0||index>=state.queue.length)return;state.index=index;state.currentTime=0;setSource(state.queue[index],0,true);save()}
  function toggle(){ensure();const m=active();if(!m)return;if(m.paused)m.play().catch(()=>{});else m.pause()}
  function previous(){ensure();if(!state.queue.length)return;if(active()?.currentTime>3){active().currentTime=0;return}playIndex((state.index-1+state.queue.length)%state.queue.length)}
  function next(){ensure();if(!state.queue.length)return;playIndex((state.index+1)%state.queue.length)}
  function sync(){const m=active();if(!m)return;const d=Number.isFinite(m.duration)?m.duration:0,c=Number.isFinite(m.currentTime)?m.currentTime:0;seek.value=d?String(Math.round(c/d*1000)):'0';time.textContent=`${fmt(c)} / ${fmt(d)}`;state.currentTime=c;save()}
  function openVideo(){ensure();if(current()?.kind!=='video')return;videoShell.classList.add('open');if(video.paused)video.play().catch(()=>{})}
  function clear(){ensure();audio.pause();video.pause();audio.removeAttribute('src');video.removeAttribute('src');state.queue=[];state.index=-1;state.currentTime=0;state.playing=false;root.style.display='none';document.querySelector('.pp-queue')?.classList.remove('open');save()}
  function info(){return JSON.parse(JSON.stringify({...state,current:current()}))}
  window.PulsarPlayer={play,enqueue,playIndex,next,previous,toggle,clear,openVideo,state:info};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});else ensure();
})();
"""


@router.get("/pulsar-player-ui.js", include_in_schema=False)
def pulsar_player_ui():
    return Response(
        content=PULSAR_PLAYER_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


class PulsarPlayerMiddleware(BaseHTTPMiddleware):
    """Inject the persistent player into signed-in member HTML across the one-site app."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET":
            return response
        # Most private routes receive request.state.member from MembershipAccessMiddleware.
        # Dashboard/Studio are deliberate public-entry shells that perform their own auth,
        # so an existing member session cookie is also sufficient to render the harmless UI.
        if getattr(request.state, "member", None) is None and not request.cookies.get("lss_session"):
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        marker = "<script src='/creative/pulsar-player-ui.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "PulsarPlayerMiddleware", "PULSAR_PLAYER_SCRIPT"]
