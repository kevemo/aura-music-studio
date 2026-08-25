from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .social_management_portal import social_house as base_social_house

router = APIRouter()

PLATFORM_AWARE_UI = r"""
<script id="espPlatformAwarePostEditor">
(()=>{
  const endpoint='/command-center/api/social/platforms';let caps=null,loading=null;
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function pretty(v){return String(v||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase())}
  async function capabilities(){if(caps)return caps;if(loading)return loading;loading=fetch(endpoint,{credentials:'same-origin'}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Platform registry failed (${r.status})`);caps=b.capabilities||{};return caps}).finally(()=>loading=null);return loading}
  function updateTypes(platform,type,info){const spec=caps?.[platform.value]||{},types=Array.isArray(spec.content_types)?spec.content_types:[];const old=type.value;type.innerHTML=types.map(v=>`<option value="${esc(v)}">${esc(pretty(v))}</option>`).join('');if(types.includes(old))type.value=old;const caption=spec.caption_limit==null?'No registry caption limit':`${Number(spec.caption_limit).toLocaleString()} character caption limit`;const media=spec.max_media==null?'No registry media limit':`up to ${spec.max_media} media`;const publish=spec.auto_publish===false?'planning only':'official adapter + authorisation required for publishing';info.textContent=`${caption} · ${media} · ${publish}`}
  async function enhance(){const platform=document.getElementById('postPlatform'),type=document.getElementById('postType');if(!platform||!type||platform.dataset.registryReady==='1')return;try{await capabilities();const keys=Object.keys(caps||{}).filter(k=>caps[k]?.planning!==false);if(!keys.length)return;const selected=caps[platform.value]?platform.value:keys[0];platform.innerHTML=keys.map(v=>`<option value="${esc(v)}">${esc(pretty(v))}</option>`).join('');platform.value=selected;platform.dataset.registryReady='1';let info=document.getElementById('postPlatformRules');if(!info){info=document.createElement('div');info.id='postPlatformRules';info.className='muted';info.style.cssText='font-size:.68rem;line-height:1.4;margin-top:-2px';type.closest('.row')?.insertAdjacentElement('afterend',info)}const sync=()=>updateTypes(platform,type,info);platform.addEventListener('change',sync);sync()}catch(error){console.warn('ESP platform-aware planner unavailable',error)}}
  const observer=new MutationObserver(()=>enhance());observer.observe(document.documentElement,{subtree:true,childList:true});enhance();
})();
</script>
"""


@router.get("/command-center/social", response_class=HTMLResponse, include_in_schema=False)
def social_house_with_intelligence(request: Request):
    """Preserve the mature Social Management UI and add intelligence/planning upgrades."""
    response = base_social_house(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    marker = "<a class='btn optional' href='/command-center/niche'>Change Niche</a>"
    extra = "<a class='btn primary' href='/command-center/social-insights'>Analytics & Aura Insights</a>"
    if marker in html and extra not in html:
        html = html.replace(marker, marker + extra, 1)
    if "id=\"espPlatformAwarePostEditor\"" not in html and "</body>" in html:
        html = html.replace("</body>", PLATFORM_AWARE_UI + "</body>", 1)
    return HTMLResponse(html, status_code=response.status_code)


__all__ = ["router", "PLATFORM_AWARE_UI"]
