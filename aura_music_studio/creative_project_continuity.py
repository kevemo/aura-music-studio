from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["creative-project-continuity"])

_CONTINUITY_PATHS = {
    "/creative-house",
    "/image-designer",
    "/video-studio",
    "/studio",
    "/game-creation",
}

PROJECT_CONTINUITY_SCRIPT = r"""
(()=>{
  const path=location.pathname;
  const TARGETS=new Set(['/creative-house','/image-designer','/video-studio','/studio','/game-creation']);
  if(!TARGETS.has(path))return;

  const requested=(new URLSearchParams(location.search).get('project')||'').trim();
  const isHouse=path==='/creative-house';
  const isMedia=path==='/image-designer'||path==='/video-studio';
  const isMusic=path==='/studio';
  const isGame=path==='/game-creation';
  const $=id=>document.getElementById(id);
  let contextualProject=requested;

  function musicProject(){
    if(!isMusic)return '';
    try{return typeof selectedProject!=='undefined'&&selectedProject?String(selectedProject).trim():''}catch(_){return ''}
  }

  function formProject(){
    const el=isHouse?$('projectName'):isMedia?$('project'):null;
    return String(el?.value||'').trim();
  }

  function currentProject(){
    return formProject()||musicProject()||contextualProject||'';
  }

  function projectHref(target,projectName=currentProject()){
    const clean=String(projectName||'').trim();
    return target+(clean?`?project=${encodeURIComponent(clean)}`:'');
  }

  function updateLocation(projectName){
    const clean=String(projectName||'').trim();
    contextualProject=clean;
    const url=new URL(location.href);
    if(clean)url.searchParams.set('project',clean);else url.searchParams.delete('project');
    history.replaceState(history.state,'',url.pathname+url.search+url.hash);
  }

  function preserveExistingLinks(projectName=currentProject()){
    const clean=String(projectName||'').trim();
    document.querySelectorAll('a[href]').forEach(link=>{
      const raw=link.getAttribute('href');
      if(!raw||raw.startsWith('#')||raw.startsWith('javascript:'))return;
      let url;
      try{url=new URL(raw,location.origin)}catch(_){return}
      if(url.origin!==location.origin||!TARGETS.has(url.pathname))return;
      if(clean)url.searchParams.set('project',clean);else url.searchParams.delete('project');
      link.setAttribute('href',url.pathname+url.search+url.hash);
    });
  }

  function ensureWorkspaceBar(){
    let bar=$('creativeProjectContinuity');
    if(bar)return bar;
    bar=document.createElement('section');
    bar.id='creativeProjectContinuity';
    bar.setAttribute('aria-label','Creative project workspace');
    bar.style.cssText='margin:10px 0 16px;padding:11px 13px;border:1px solid #ffffff24;border-radius:15px;background:#0b0d17dd;color:#fff;display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-family:Inter,ui-sans-serif,system-ui,sans-serif;box-shadow:0 10px 30px #0003';
    bar.innerHTML=`<strong style="color:#f3c76d">One Project Workspace</strong><span id="creativeProjectContext" style="font-size:.78rem;color:#c9c6d5"></span><nav id="creativeProjectLinks" style="display:flex;gap:6px;flex-wrap:wrap;margin-left:auto"></nav>`;
    const host=document.querySelector('main.wrap')||document.querySelector('.wrap')||document.body;
    const top=host.querySelector?.('.top,header,.hero');
    if(top?.parentNode===host)top.insertAdjacentElement('afterend',bar);else host.insertBefore(bar,host.firstChild);
    return bar;
  }

  function drawWorkspace(){
    ensureWorkspaceBar();
    const projectName=currentProject();
    const context=$('creativeProjectContext');
    if(context)context.textContent=projectName?`Project: ${projectName}`:'No project selected yet — choose or load one and it will follow you across studios.';
    const nav=$('creativeProjectLinks');
    if(nav){
      const items=[
        ['/creative-house','Creative House'],
        ['/image-designer','Image'],
        ['/video-studio','Video'],
        ['/studio','Music'],
        ['/game-creation','Game Forge'],
      ];
      nav.replaceChildren(...items.map(([href,label])=>{
        const a=document.createElement('a');
        a.href=projectHref(href,projectName);
        a.textContent=label;
        a.style.cssText='color:#fff;text-decoration:none;border:1px solid #ffffff24;border-radius:999px;padding:6px 9px;font-size:.72rem;font-weight:800;background:#ffffff08';
        if(path===href){a.setAttribute('aria-current','page');a.style.borderColor='#f3c76d88';a.style.color='#f3c76d'}
        return a;
      }));
    }
    preserveExistingLinks(projectName);
  }

  function commitProject(projectName){
    const clean=String(projectName||'').trim();
    if(!clean)return drawWorkspace();
    updateLocation(clean);
    drawWorkspace();
  }

  function wrapProjectFunctions(){
    if((isHouse||isMedia)&&typeof loadProject==='function'){
      const baseLoad=loadProject;
      loadProject=async function(...args){
        const out=await baseLoad(...args);
        if(out!==null&&currentProject())commitProject(currentProject());else drawWorkspace();
        return out;
      };
    }
    if(isHouse&&typeof initializeProject==='function'){
      const baseInit=initializeProject;
      initializeProject=async function(...args){const out=await baseInit(...args);if(currentProject())commitProject(currentProject());return out};
    }
    if(isMedia&&typeof initProject==='function'){
      const baseInit=initProject;
      initProject=async function(...args){const out=await baseInit(...args);if(currentProject())commitProject(currentProject());return out};
    }
    if(isMusic&&typeof selectProject==='function'){
      const baseSelect=selectProject;
      selectProject=function(name,...args){const out=baseSelect(name,...args);commitProject(name);return out};
    }
  }

  async function bootRequestedProject(){
    if(!requested)return drawWorkspace();
    if(isMedia){
      const input=$('project');
      if(input)input.value=requested;
      if(typeof loadProject==='function')await loadProject(true);
      return drawWorkspace();
    }
    if(isHouse){
      // Creative House already honors ?project= natively. Keep the same URL/context after its load.
      const input=$('projectName');
      if(input&&!input.value)input.value=requested;
      return drawWorkspace();
    }
    if(isMusic){
      if(typeof refreshProjects!=='function'||typeof selectProject!=='function')return drawWorkspace();
      try{
        await refreshProjects();
        const exists=typeof projects!=='undefined'&&Array.isArray(projects)&&projects.some(item=>String(item?.name||'')===requested);
        if(exists)selectProject(requested);
      }catch(_){/* Music Studio keeps its native project error handling. */}
      return drawWorkspace();
    }
    if(isGame){
      // Game Forge consumes verified Creative Library assets but has no stable Creative Project key.
      // Carry the exact context in navigation without inferring identity from human-readable labels.
      contextualProject=requested;
      return drawWorkspace();
    }
    drawWorkspace();
  }

  wrapProjectFunctions();
  drawWorkspace();
  void bootRequestedProject();

  document.addEventListener('change',event=>{
    if((isHouse&&event.target?.id==='projectName')||(isMedia&&event.target?.id==='project'))drawWorkspace();
  });

  window.CreativeProjectContinuity={
    currentProject,
    commitProject,
    projectHref,
    refresh:drawWorkspace,
  };
})();
"""


@router.get("/creative/project-continuity-ui.js", include_in_schema=False)
def creative_project_continuity_ui():
    return Response(
        content=PROJECT_CONTINUITY_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


class CreativeProjectContinuityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path not in _CONTINUITY_PATHS:
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
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )

        marker = "<script src='/creative/project-continuity-ui.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = [
    "router",
    "CreativeProjectContinuityMiddleware",
    "PROJECT_CONTINUITY_SCRIPT",
]
