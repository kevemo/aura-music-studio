from __future__ import annotations

from typing import Any

from . import shared_sky_professional_canvas as canvas


MOTION_CSS = r"""
.motion-ticker{width:100%;height:100%;display:flex;align-items:center;overflow:hidden;white-space:nowrap}.motion-ticker-track{display:inline-block;min-width:100%;padding-left:100%;will-change:transform;animation:shared-sky-ticker var(--ticker-speed,18s) linear infinite}.motion-ticker.right .motion-ticker-track{animation-name:shared-sky-ticker-right}.motion-countdown{width:100%;height:100%;display:grid;place-items:center;text-align:center}.motion-countdown .count-label{font-size:.45em;opacity:.85;display:block}.motion-countdown .count-value{font-variant-numeric:tabular-nums;display:block}.motion-countdown.complete .count-value{font-size:.78em}
@keyframes shared-sky-ticker{from{transform:translateX(0)}to{transform:translateX(-200%)}}@keyframes shared-sky-ticker-right{from{transform:translateX(-200%)}to{transform:translateX(0)}}
@media(prefers-reduced-motion:reduce){.motion-ticker-track{animation:none!important;transform:none!important;padding-left:0!important;white-space:normal}}
"""

MOTION_BUTTONS = r"""<button id='addTicker'>Ticker</button><button id='addCountdown'>Countdown</button>"""

MOTION_JS = r"""
const baseMotionGraphicMedia=graphicMedia;
function boundedNumber(value,fallback,min,max){const n=Number(value);return Number.isFinite(n)?Math.max(min,Math.min(max,n)):fallback;}
function safeGraphicHex(value,fallback){const v=String(value||'').toLowerCase();return /^#[0-9a-f]{6}$/.test(v)?v:fallback;}
function safeGraphicAlign(value){const v=String(value||'left');return ['left','center','right'].includes(v)?v:'left';}
function motionStyle(g){const s=g.style||{},font=boundedNumber(s.font_size,42,8,200),weight=boundedNumber(s.font_weight,700,100,900),align=safeGraphicAlign(s.align),text=safeGraphicHex(s.text_color,'#ffffff'),background=safeGraphicHex(s.background_color,'#07131c'),opacity=boundedNumber(s.background_opacity,.78,0,1),padding=boundedNumber(s.padding,18,0,80),radius=boundedNumber(s.corner_radius,14,0,80);return `font-size:${font}px;font-weight:${weight};text-align:${align};color:${text};background:${rgba(background,opacity)};padding:${padding}px;border-radius:${radius}px`;}
function staticGraphicMedia(src,g){const kind=String(g.kind||'custom_text').replace(/[^a-z0-9_-]/gi,'').slice(0,40)||'custom_text';return `<div class='graphic graphic-${kind}' style='${motionStyle(g)}'><strong>${esc(g.text||src.config?.text||src.name)}</strong>${g.secondary_text?`<span>${esc(g.secondary_text)}</span>`:''}</div>`;}
function tickerText(g){if(g.binding==='transport_state')return `${g.prefix||'Transport'} ${String(state.transport?.state||'unavailable').toUpperCase()}`;if(g.binding==='recording_state'){const rec=(state.transport?.recordings||[]).find(r=>r.kind==='programme');return `${g.prefix||'Recording'} ${String(rec?.state||'idle').toUpperCase()}`;}return (g.items||[]).map(v=>String(v).slice(0,160)).join(String(g.separator||' • ').slice(0,12));}
graphicMedia=function(src){const g=src.config?.graphic;if(g?.kind==='ticker'){const text=tickerText(g),speed=boundedNumber(g.speed_seconds,18,4,120);return `<div class='motion-ticker ${g.direction==='right'?'right':''}' style='${motionStyle(g)};--ticker-speed:${speed}s'><div class=motion-ticker-track>${esc(text||'Waiting for authoritative data')}</div></div>`;}if(g?.kind==='countdown'){return `<div class=motion-countdown data-countdown-target='${esc(String(g.target_at||'').slice(0,80))}' data-countdown-label='${esc(String(g.label||'Starting in').slice(0,120))}' data-countdown-complete='${esc(String(g.complete_text||'Starting now').slice(0,120))}' data-countdown-days='${g.show_days!==false?'1':'0'}' style='${motionStyle(g)}'><div><span class=count-label>${esc(String(g.label||'Starting in').slice(0,120))}</span><strong class=count-value>—</strong></div></div>`;}if(g)return staticGraphicMedia(src,g);return baseMotionGraphicMedia(src);};
function countdownValue(ms,showDays){if(ms<=0)return null;let total=Math.floor(ms/1000),days=Math.floor(total/86400);total%=86400;const h=Math.floor(total/3600);total%=3600;const m=Math.floor(total/60),s=total%60;const mm=String(m).padStart(2,'0'),ss=String(s).padStart(2,'0');return showDays&&days?`${days}d ${String(h).padStart(2,'0')}:${mm}:${ss}`:`${String(h+days*24).padStart(2,'0')}:${mm}:${ss}`;}
function updateCountdowns(){for(const node of $$('[data-countdown-target]')){const target=Date.parse(node.dataset.countdownTarget||''),value=node.querySelector('.count-value'),label=node.querySelector('.count-label');if(!Number.isFinite(target)||!value)continue;const remaining=target-Date.now(),formatted=countdownValue(remaining,node.dataset.countdownDays==='1');if(formatted===null){node.classList.add('complete');if(label)label.textContent='';value.textContent=node.dataset.countdownComplete||'Starting now';}else{node.classList.remove('complete');if(label)label.textContent=node.dataset.countdownLabel||'Starting in';value.textContent=formatted;}}}
const baseMotionRender=render;render=function(){baseMotionRender();updateCountdowns();};setInterval(updateCountdowns,1000);
async function addTickerUI(){const binding=(prompt('Ticker binding: static, transport_state, or recording_state','static')||'static').trim();let items=[];if(binding==='static'){const raw=prompt('Ticker items separated by |','Welcome to Shared Sky | Elevate Your Soul Through Purposeful Media');if(raw===null)return;items=raw.split('|').map(x=>x.trim()).filter(Boolean);}const prefix=binding==='static'?'':(prompt('Ticker label/prefix',binding==='transport_state'?'Transport':'Recording')||'');const speed=Number(prompt('Scroll duration in seconds (4–120)','18')||18);try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/graphics/ticker`,{method:'POST',body:JSON.stringify({name:'Shared Sky Ticker',items,binding,prefix,speed_seconds:speed,direction:'left',style:{font_size:34},expected_version:state.session.version})});await refreshProject();$('#notice').textContent='Ticker added to Preview. Programme is unchanged until CUT/TRANSITION.';}catch(e){handle(e);}}
async function addCountdownUI(){const target=prompt('Countdown target in ISO 8601 with timezone (example 2026-09-05T20:00:00+01:00)','');if(!target)return;const label=prompt('Countdown label','Starting in')||'Starting in';const complete=prompt('Text when countdown completes','Starting now')||'Starting now';try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/graphics/countdown`,{method:'POST',body:JSON.stringify({name:'Shared Sky Countdown',label,target_at:target,complete_text:complete,show_days:true,style:{font_size:54,align:'center'},expected_version:state.session.version})});await refreshProject();$('#notice').textContent='Countdown added to Preview. Programme is unchanged until CUT/TRANSITION.';}catch(e){handle(e);}}
$('#addTicker').onclick=addTickerUI;$('#addCountdown').onclick=addCountdownUI;
"""


def enhanced_motion_html(project_id: str, base_html) -> str:
    html = base_html(project_id)
    if "id='addTicker'" in html:
        return html
    html = html.replace("</style>", MOTION_CSS + "</style>", 1)
    html = html.replace(
        "<button id='addBanner'>Banner</button>",
        "<button id='addBanner'>Banner</button>" + MOTION_BUTTONS,
        1,
    )
    html = html.replace("</script></body>", MOTION_JS + "</script></body>", 1)
    return html


def install_professional_motion_graphics_ui(app: Any) -> None:
    del app
    current = canvas.professional_html
    if getattr(current, "_shared_sky_motion_graphics_ui", False):
        return

    def wrapped(project_id: str) -> str:
        return enhanced_motion_html(project_id, current)

    for marker in (
        "_shared_sky_operator_ui",
        "_shared_sky_transport_toolbar",
        "_shared_sky_ingest_ui",
        "_shared_sky_motion_graphics_ui",
    ):
        if marker == "_shared_sky_motion_graphics_ui" or getattr(current, marker, False):
            setattr(wrapped, marker, True)
    canvas.professional_html = wrapped


__all__ = [
    "MOTION_BUTTONS",
    "MOTION_CSS",
    "MOTION_JS",
    "enhanced_motion_html",
    "install_professional_motion_graphics_ui",
]
