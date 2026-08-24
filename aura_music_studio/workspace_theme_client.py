from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["Workspace Theme Client"])

THEME_CLIENT_JS = r"""
(()=>{
  if(window.__auraWorkspaceThemeClient)return;
  window.__auraWorkspaceThemeClient=true;
  const previewId='workspace-theme-preview';
  const savedId='workspace-theme-tokens';
  function styleNode(id){let node=document.getElementById(id);if(!node){node=document.createElement('style');node.id=id;document.head.appendChild(node)}return node}
  function applyPreview(css){if(typeof css!=='string'||!css.trim())return;styleNode(previewId).textContent=css;document.documentElement.dataset.themePreview='true'}
  function clearPreview(){const node=document.getElementById(previewId);if(node)node.remove();delete document.documentElement.dataset.themePreview}
  function applySaved(css){if(typeof css==='string'&&css.trim())styleNode(savedId).textContent=css;clearPreview()}
  function handle(event){
    if(!event||event.status!=='completed')return;
    const result=event.result||{};
    if(event.tool==='preview_workspace_theme'&&result.css){applyPreview(result.css);window.dispatchEvent(new CustomEvent('aura:theme-preview',{detail:result}));return}
    if(event.tool==='confirm_workspace_theme'&&result.css){applySaved(result.css);window.dispatchEvent(new CustomEvent('aura:theme-confirmed',{detail:result}));return}
    if(event.tool==='discard_workspace_theme_preview'){clearPreview();window.dispatchEvent(new CustomEvent('aura:theme-discarded',{detail:result}));return}
    if(event.tool==='revert_workspace_theme'&&result.css){applySaved(result.css);window.dispatchEvent(new CustomEvent('aura:theme-reverted',{detail:result}))}
  }
  function inspect(payload){if(!payload||!Array.isArray(payload.tool_events))return;payload.tool_events.forEach(handle)}
  const originalFetch=window.fetch.bind(window);
  window.fetch=async(...args)=>{
    const response=await originalFetch(...args);
    try{
      const raw=args[0];const url=typeof raw==='string'?raw:(raw&&raw.url)||'';
      if(url.includes('/api/aura/chat')){const clone=response.clone();const type=clone.headers.get('content-type')||'';if(type.includes('application/json'))inspect(await clone.json())}
    }catch(_err){}
    return response;
  };
  window.addEventListener('aura:workspace-theme-result',event=>handle(event.detail));
})();
"""


@router.get("/brand/workspace-theme-client.js", include_in_schema=False)
def workspace_theme_client() -> Response:
    return Response(
        THEME_CLIENT_JS,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


class WorkspaceThemeClientMiddleware(BaseHTTPMiddleware):
    """Load the preview client only for sessions that may have a personalized workspace."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400 or "text/html" not in (response.headers.get("content-type") or "").lower():
            return response
        if not (request.cookies.get("lss_session") or request.cookies.get("lss_admin_session")):
            return response
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
        raw = b"".join(chunks)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            return Response(raw, status_code=response.status_code, headers=headers, media_type=response.headers.get("content-type"))
        script = "<script src='/brand/workspace-theme-client.js' defer></script>"
        if "workspace-theme-client.js" not in text:
            if "</head>" in text:
                text = text.replace("</head>", script + "</head>", 1)
            elif "</body>" in text:
                text = text.replace("</body>", script + "</body>", 1)
            else:
                text += script
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        return HTMLResponse(text, status_code=response.status_code, headers=headers)
