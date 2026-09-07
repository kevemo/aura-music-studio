from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .shared_sky_live_watch_bridge_guard import watch_page_bridge_guard


router = APIRouter(tags=["Shared Sky Watch Engagement Wave 6"])

_GIFT_MARKER = "<div class='info'><h2>Cosmic Creation Coin Gifts</h2>"
_ENHANCED_GIFT_MARKER = "<div class='info' id='giftLivePanel'><h2>Cosmic Creation Coin Gifts</h2>"
_BATTLE_STATIC_MARKER = "<div class='info'><span class='badge'>Battle</span>"
_BATTLE_STATIC_REPLACEMENT = "<div class='info' id='battleInitialPanel'><span class='badge'>Battle</span>"
_ENHANCEMENT_ID = "sharedSkyEngagementWave6"


def _engagement_script(broadcast_id: str) -> str:
    # JSON is inert data inside a script element only if HTML-closing sequences cannot escape it.
    # Keep the canonical route value intact at JavaScript parse time while breaking `</script>`.
    broadcast_json = json.dumps(broadcast_id).replace("</", "<\\/")
    return f"""
<script id='{_ENHANCEMENT_ID}'>
(()=>{{
'use strict';
const broadcastId={broadcast_json};
const giftPanel=document.getElementById('giftLivePanel');
const battlePanel=document.getElementById('battleLivePanel');
const initialBattle=document.getElementById('battleInitialPanel');
const announce=document.getElementById('announce');
let giftState=null;
let battleState=null;
let giftBusy=false;
let battleTimer=null;

function say(message){{if(announce)announce.textContent=String(message||'')}}
function el(tag,className,text){{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node}}
function errorMessage(payload,fallback){{
  const detail=payload&&typeof payload==='object'?payload.detail:null;
  if(detail&&typeof detail==='object')return String(detail.message||detail.code||fallback);
  if(typeof detail==='string')return detail;
  return fallback;
}}
async function jsonFetch(url,options){{
  const response=await fetch(url,options);
  let payload=null;
  try{{payload=await response.json()}}catch(_error){{payload=null}}
  if(!response.ok){{const err=new Error(errorMessage(payload,'Request failed'));err.status=response.status;err.payload=payload;throw err}}
  return payload;
}}
function availableCoins(state){{
  const value=state&&state.balance&&state.balance.available_coins;
  return Number.isFinite(Number(value))?Number(value):null;
}}
function giftButtonLabel(item){{return `Send · ${{Number(item.coin_cost||0).toLocaleString()}} Coins`}}
function renderGifts(state){{
  if(!giftPanel)return;
  let host=document.getElementById('giftWave6Controls');
  if(!host){{host=el('div');host.id='giftWave6Controls';giftPanel.appendChild(host)}}
  host.replaceChildren();
  const status=el('p','muted');
  const coins=availableCoins(state);
  if(coins!==null)status.textContent=`Available balance: ${{coins.toLocaleString()}} Cosmic Creation Coins.`;
  else status.textContent=state&&state.reason?`Gift sending: ${{String(state.reason)}}.`:'Gift state is loading.';
  host.appendChild(status);
  const catalogue=state&&Array.isArray(state.catalogue)?state.catalogue:[];
  if(!catalogue.length){{host.appendChild(el('p','muted','No active LIVE Gifts are available for this session.'));return}}
  const grid=el('div','gift-grid');
  for(const item of catalogue.slice(0,40)){{
    if(!item||typeof item!=='object'||!item.gift_id||!Number(item.version))continue;
    const card=el('div','gift');
    card.appendChild(el('b','',item.display_name||'LIVE Gift'));
    if(item.description)card.appendChild(el('div','muted',item.description));
    const cost=Math.max(0,Number(item.coin_cost||0));
    const button=el('button','primary',giftButtonLabel(item));
    button.type='button';
    const enough=coins===null||coins>=cost;
    button.disabled=giftBusy||!state.send_enabled||!enough;
    if(!enough)button.title='Your current Cosmic Creation Coin balance is below this Gift cost.';
    button.addEventListener('click',()=>sendGift(item,button));
    card.appendChild(button);
    grid.appendChild(card);
  }}
  host.appendChild(grid);
}}
function idempotencyKey(){{
  if(globalThis.crypto&&typeof globalThis.crypto.randomUUID==='function')return globalThis.crypto.randomUUID();
  const bytes=new Uint32Array(4);
  if(globalThis.crypto&&typeof globalThis.crypto.getRandomValues==='function')globalThis.crypto.getRandomValues(bytes);
  else for(let i=0;i<bytes.length;i++)bytes[i]=Math.floor(Math.random()*0xffffffff);
  return `shared-sky-gift-${{Date.now()}}-${{Array.from(bytes).map(v=>v.toString(16)).join('')}}`;
}}
async function refreshGifts(){{
  try{{giftState=await jsonFetch(`/shared-sky/live/api/watch/${{encodeURIComponent(broadcastId)}}/gift-display`,{{credentials:'same-origin',headers:{{'Accept':'application/json'}}}});renderGifts(giftState)}}
  catch(error){{if(giftPanel){{let host=document.getElementById('giftWave6Controls');if(!host){{host=el('div');host.id='giftWave6Controls';giftPanel.appendChild(host)}}host.replaceChildren(el('p','muted',error.status===401?'Sign in to use LIVE Gifts.':'LIVE Gift state is temporarily unavailable.'))}}}}
}}
async function sendGift(item,button){{
  if(giftBusy||!giftState||!giftState.send_enabled)return;
  giftBusy=true;
  renderGifts(giftState);
  if(button)button.disabled=true;
  const payload={{
    recipient_creator_id:String(giftState.recipient_creator_id||''),
    live_session_id:String(giftState.live_session_id||broadcastId),
    gift_id:String(item.gift_id),
    gift_version:Number(item.version),
    quantity:1
  }};
  try{{
    await jsonFetch('/economy/me/gifts/send',{{
      method:'POST',credentials:'same-origin',
      headers:{{'Accept':'application/json','Content-Type':'application/json','Idempotency-Key':idempotencyKey()}},
      body:JSON.stringify(payload)
    }});
    say(`${{item.display_name||'Gift'}} sent successfully.`);
  }}catch(error){{say(error.status===401?'Sign in to send a LIVE Gift.':`Gift not sent: ${{error.message||'request rejected'}}`)}}
  finally{{giftBusy=false;await refreshGifts()}}
}}
function participantKey(item){{return String(item&&(item.participant_id||item.creator_user_id||item.user_id||item.id)||'')}}
function participantName(item){{return String(item&&(item.display_name||item.name||item.creator_display_name||item.creator_user_id||item.user_id)||'Creator')}}
function scoreFor(scores,key){{
  if(!scores||typeof scores!=='object'||!key)return null;
  const raw=scores[key];
  if(Number.isFinite(Number(raw)))return Number(raw);
  if(raw&&typeof raw==='object'){{const value=raw.score??raw.total??raw.points;if(Number.isFinite(Number(value)))return Number(value)}}
  return null;
}}
function renderBattle(state){{
  if(!battlePanel)return;
  if(!state||!state.available){{battlePanel.hidden=true;if(initialBattle)initialBattle.hidden=false;return}}
  battlePanel.hidden=false;if(initialBattle)initialBattle.hidden=true;
  battlePanel.replaceChildren();
  const top=el('div','top');
  const title=el('div');
  const badge=el('span','badge','Creator Battle');
  title.appendChild(badge);
  title.appendChild(document.createTextNode(` ${{String(state.status||'LIVE')}}`));
  top.appendChild(title);
  if(Number.isFinite(Number(state.remaining_ms)))top.appendChild(el('span','muted',`${{Math.max(0,Math.ceil(Number(state.remaining_ms)/1000))}}s remaining`));
  battlePanel.appendChild(top);
  const participants=Array.isArray(state.participants)?state.participants:[];
  const scores=state.scores&&typeof state.scores==='object'?state.scores:{{}};
  if(participants.length){{
    const grid=el('div','gift-grid');
    for(const participant of participants){{
      if(!participant||typeof participant!=='object')continue;
      const card=el('div','gift');
      card.appendChild(el('b','',participantName(participant)));
      const score=scoreFor(scores,participantKey(participant));
      if(score!==null)card.appendChild(el('div','muted',`${{score.toLocaleString()}} points`));
      grid.appendChild(card);
    }}
    battlePanel.appendChild(grid);
  }}
  if(state.result)battlePanel.appendChild(el('p','muted','Battle result is available from the authoritative Battle service.'));
}}
async function refreshBattle(){{
  try{{battleState=await jsonFetch(`/shared-sky/live/api/watch/${{encodeURIComponent(broadcastId)}}/battle-display`,{{credentials:'same-origin',headers:{{'Accept':'application/json'}}}});renderBattle(battleState)}}
  catch(_error){{renderBattle(null)}}
  clearTimeout(battleTimer);
  battleTimer=setTimeout(refreshBattle,battleState&&battleState.available?2000:10000);
}}

refreshGifts();
refreshBattle();
setInterval(refreshGifts,15000);
addEventListener('pagehide',()=>{{if(battleTimer)clearTimeout(battleTimer)}});
}})();
</script>
"""


def _enhance_watch_html(html: str, broadcast_id: str) -> str:
    """Add viewer-side Gift sending and Battle refresh without creating new authorities.

    The page continues to call Chat 5's canonical Gift mutation endpoint and Chat 6's read-only
    Battle projection. If the expected Watch-v2 HTML contract drifts, this enhancer returns the
    original page unchanged rather than guessing at a new structure.
    """

    if _ENHANCEMENT_ID in html or _GIFT_MARKER not in html or "</body>" not in html:
        return html

    enhanced = html.replace(_GIFT_MARKER, _ENHANCED_GIFT_MARKER, 1)
    if _BATTLE_STATIC_MARKER in enhanced:
        enhanced = enhanced.replace(_BATTLE_STATIC_MARKER, _BATTLE_STATIC_REPLACEMENT, 1)
    battle_panel = "<div id='battleLivePanel' class='info' hidden aria-live='polite'></div>"
    enhanced = enhanced.replace(_ENHANCED_GIFT_MARKER, battle_panel + _ENHANCED_GIFT_MARKER, 1)
    return enhanced.replace("</body>", _engagement_script(broadcast_id) + "</body>", 1)


@router.get("/watch/{broadcast_id}", response_class=HTMLResponse, include_in_schema=False)
def watch_page_engagement_wave6(broadcast_id: str, request: Request):
    response = watch_page_bridge_guard(broadcast_id, request)
    if response.status_code != 200:
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    enhanced = _enhance_watch_html(html, broadcast_id)
    if enhanced == html:
        return response
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers["Cache-Control"] = "no-store"
    return HTMLResponse(enhanced, status_code=response.status_code, headers=headers)


__all__ = ["router", "watch_page_engagement_wave6", "_enhance_watch_html"]
