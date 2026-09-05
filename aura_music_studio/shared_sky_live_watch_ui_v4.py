from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .shared_sky_live_watch_ui_v2 import watch_page_v2

router = APIRouter(tags=["Shared Sky Watch Player Wave 4"])

_BROWSER_EXCHANGE_SCRIPT = r"""
<script>
(()=>{
  const match=location.pathname.match(/^\/watch\/([^/?#]+)$/);
  if(!match)return;
  let broadcastId='';
  try{broadcastId=decodeURIComponent(match[1])}catch(_){return}
  const encoded=encodeURIComponent(broadcastId);
  const playbackPath=`/shared-sky/live/api/watch/${encoded}/playback`;
  const sessionPath=`/shared-sky/live/api/watch/${encoded}/browser-playback-session`;
  const cookiePath=`/shared-sky/media/${encoded}/authorize`;
  const originalFetch=window.fetch.bind(window);
  let secureExchangeUnavailable=false;

  window.fetch=async(input,init={})=>{
    let url='';
    try{url=typeof input==='string'?input:String(input&&input.url||'')}catch(_){url=''}
    const method=String(init&&init.method||'GET').toUpperCase();
    let pathname='';
    try{pathname=new URL(url,location.origin).pathname}catch(_){pathname=url.split('?')[0]}
    if(!secureExchangeUnavailable&&method==='GET'&&pathname===playbackPath){
      const session=await originalFetch(sessionPath,{
        method:'POST',
        credentials:'same-origin',
        headers:{'Accept':'application/json','X-Shared-Sky-Playback-Intent':'watch'},
        cache:'no-store'
      });
      if(session.ok)return session;
      if(session.status===503){
        secureExchangeUnavailable=true;
      }else{
        return session;
      }
    }
    return originalFetch(input,init);
  };

  // The Wave 2 page may already have rendered its fail-closed playback state before this additive
  // compatibility script runs. Trigger the existing bounded refresh once after installing the
  // secure exchange interception; no new player state machine is introduced here.
  queueMicrotask(()=>{
    const retry=document.querySelector('#playerStatus button');
    if(retry&&!retry.disabled)retry.click();
  });

  addEventListener('pagehide',()=>{
    originalFetch(cookiePath,{
      method:'DELETE',
      credentials:'same-origin',
      keepalive:true,
      cache:'no-store'
    }).catch(()=>{});
  });
})();
</script>
"""


@router.get("/watch/{broadcast_id}", response_class=HTMLResponse, include_in_schema=False)
def watch_page_v4(broadcast_id: str, request: Request):
    """Decorate the validated Wave 2 Watch page with Chat 2's secure browser-cookie exchange.

    The Wave 2 page remains the UI/state implementation. This wrapper adds only the neighbour
    compatibility behaviour needed to turn its access-checked playback refresh into a dedicated
    POST cookie-exchange attempt. If Chat 2 Wave 2 is not installed, the POST returns 503 once and
    the existing Wave 2 fail-closed GET descriptor path is used unchanged.
    """

    response = watch_page_v2(broadcast_id, request)
    if response.status_code != 200:
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    marker = "</body>"
    if marker not in html:
        return response
    html = html.replace(marker, _BROWSER_EXCHANGE_SCRIPT + marker, 1)
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    headers["Cache-Control"] = "no-store"
    headers["Referrer-Policy"] = "no-referrer"
    return HTMLResponse(html, status_code=200, headers=headers)


__all__ = ["router", "watch_page_v4"]
