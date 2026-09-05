from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from .game_forge_project_binding import router as game_forge_project_binding_router

router = APIRouter(tags=["creative-project-continuity"])
# Project-bound Game Forge endpoints are composed into the already-mounted continuity router so
# they remain part of the one production application without adding a parallel Game Forge app.
router.include_router(game_forge_project_binding_router)

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

  const params=new URLSearchParams(location.search);
  const requested=(new URLSearchParams(location.search).get('project')||'').trim();
  const requestedGame=(params.get('game')||'').trim();
  const isHouse=path==='/creative-house';
  const isMedia=path==='/image-designer'||path==='/video-studio';
  const isMusic=path==='/studio';
  const isGame=path==='/game-creation';
  const $=id=>document.getElementById(id);
  let contextualProject=requested;
  let gameSummaryToken=0;
  const nativeFetch=window.fetch.bind(window);

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

  function projectGameHref(gameId,projectName=currentProject()){
    const cleanProject=String(projectName||'').trim();
    const cleanGame=String(gameId||'').trim();
    const query=new URLSearchParams();
    if(cleanProject)query.set('project',cleanProject);
    if(cleanGame)query.set('game',cleanGame);
    const encoded=query.toString();
    return '/game-creation'+(encoded?`?${encoded}`:'');
  }

  function projectGameExportHref(gameId,projectName=currentProject()){
    const cleanProject=String(projectName||'').trim();
    const cleanGame=String(gameId||'').trim();
    if(!cleanGame)return projectHref('/game-creation',cleanProject);
    const query=new URLSearchParams();
    if(cleanProject)query.set('project',cleanProject);
    const encoded=query.toString();
    return `/game-creation/export/${encodeURIComponent(cleanGame)}`+(encoded?`?${encoded}`:'');
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
    bar.innerHTML=`<strong style="color:#f3c76d">One Project Workspace</strong><span id="creativeProjectContext" style="font-size:.78rem;color:#c9c6d5"></span><span id="creativeProjectGameContext" style="font-size:.72rem;color:#8fdff0"></span><a id="creativeProjectResumeGame" href="/game-creation" hidden style="color:#fff;text-decoration:none;border:1px solid #5be1ff66;border-radius:999px;padding:6px 9px;font-size:.72rem;font-weight:800;background:#5be1ff12">Resume latest game</a><a id="creativeProjectPlayGame" href="/game-creation" hidden target="_blank" rel="noopener noreferrer" style="color:#fff;text-decoration:none;border:1px solid #79dda566;border-radius:999px;padding:6px 9px;font-size:.72rem;font-weight:800;background:#79dda512">Play build</a><a id="creativeProjectPublicGame" href="/game-gallery" hidden target="_blank" rel="noopener noreferrer" style="color:#fff;text-decoration:none;border:1px solid #efca6d66;border-radius:999px;padding:6px 9px;font-size:.72rem;font-weight:800;background:#efca6d12">Public test</a><a id="creativeProjectExportGame" href="/game-creation" hidden style="color:#fff;text-decoration:none;border:1px solid #986cff66;border-radius:999px;padding:6px 9px;font-size:.72rem;font-weight:800;background:#986cff12">Export PWA</a><nav id="creativeProjectLinks" style="display:flex;gap:6px;flex-wrap:wrap;margin-left:auto"></nav>`;
    const host=document.querySelector('main.wrap')||document.querySelector('.wrap')||document.body;
    const top=host.querySelector?.('.top,header,.hero');
    if(top?.parentNode===host)top.insertAdjacentElement('afterend',bar);else host.insertBefore(bar,host.firstChild);
    return bar;
  }

  async function responseJson(response){
    let body={};
    try{body=await response.clone().json()}catch(_){}
    if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:(body.detail?.message||`Request failed (${response.status})`));
    return body;
  }

  function hideGameProjectActions(){
    const resume=$('creativeProjectResumeGame');
    const play=$('creativeProjectPlayGame');
    const publicPlay=$('creativeProjectPublicGame');
    const exportGame=$('creativeProjectExportGame');
    if(resume)resume.hidden=true;
    if(play)play.hidden=true;
    if(publicPlay)publicPlay.hidden=true;
    if(exportGame)exportGame.hidden=true;
  }

  async function refreshGameProjectSummary(projectName=currentProject()){
    ensureWorkspaceBar();
    const clean=String(projectName||'').trim();
    const token=++gameSummaryToken;
    const context=$('creativeProjectGameContext');
    const resume=$('creativeProjectResumeGame');
    const play=$('creativeProjectPlayGame');
    const publicPlay=$('creativeProjectPublicGame');
    const exportGame=$('creativeProjectExportGame');
    if(!clean){
      if(context)context.textContent='';
      hideGameProjectActions();
      return;
    }
    if(context)context.textContent='Game Forge: checking…';
    hideGameProjectActions();
    try{
      const response=await nativeFetch(`/api/game-forge/projects/${encodeURIComponent(clean)}/games`,{credentials:'same-origin'});
      const payload=await responseJson(response);
      if(token!==gameSummaryToken||currentProject()!==clean)return;
      const games=Array.isArray(payload.games)?payload.games:[];
      if(!games.length){
        if(context)context.textContent='Game Forge: no Game DNA yet';
        return;
      }
      const latest=games[0]||{};
      const exportState=latest.aura_web_export||{};
      const testState=latest.public_id?'public test':(latest.latest_build?'build ready':'not built');
      const deliveryState=exportState.ready?'export ready':'export blocked';
      if(context)context.textContent=`Game Forge: ${games.length} game${games.length===1?'':'s'} · ${String(latest.status||'draft').replaceAll('_',' ')} · ${testState} · ${deliveryState}`;
      if(resume&&latest.id){
        resume.href=projectGameHref(latest.id,clean);
        resume.textContent=games.length===1?'Resume game':'Resume latest game';
        resume.hidden=false;
      }
      if(play&&payload.can_create&&latest.id&&latest.latest_build){
        play.href=`/game-creation/play/${encodeURIComponent(String(latest.id))}`;
        play.hidden=false;
      }
      if(publicPlay&&latest.public_id){
        publicPlay.href=`/game-gallery/${encodeURIComponent(String(latest.public_id))}`;
        publicPlay.hidden=false;
      }
      if(exportGame&&payload.can_create&&latest.id&&exportState.ready&&exportState.production_ready_target){
        exportGame.href=projectGameExportHref(latest.id,clean);
        exportGame.hidden=false;
      }
    }catch(_){
      if(token!==gameSummaryToken)return;
      if(context)context.textContent='Game Forge status unavailable';
      hideGameProjectActions();
    }
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
    void refreshGameProjectSummary(projectName);
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

  function installGameProjectTransport(){
    if(!isGame)return;
    window.fetch=async function(input,init={}){
      if(typeof input!=='string')return nativeFetch(input,init);
      const url=new URL(input,location.origin);
      if(url.origin!==location.origin)return nativeFetch(input,init);
      const method=String(init.method||'GET').toUpperCase();
      const projectName=currentProject();
      if(projectName&&url.pathname==='/api/game-forge/games'&&method==='GET'){
        url.pathname=`/api/game-forge/projects/${encodeURIComponent(projectName)}/games`;
        return nativeFetch(url.pathname+url.search,init);
      }
      if(projectName&&url.pathname==='/api/game-forge/games'&&method==='POST'){
        url.pathname=`/api/game-forge/projects/${encodeURIComponent(projectName)}/games`;
        return nativeFetch(url.pathname+url.search,init);
      }
      const library=url.pathname.match(/^\/api\/game-forge\/games\/([^/]+)\/assets\/library$/);
      if(library&&method==='GET'){
        url.pathname=`/api/game-forge/games/${library[1]}/project-library`;
        return nativeFetch(url.pathname+url.search,init);
      }
      const assets=url.pathname.match(/^\/api\/game-forge\/games\/([^/]+)\/assets$/);
      if(assets&&method==='POST'){
        url.pathname=`/api/game-forge/games/${assets[1]}/project-assets`;
        return nativeFetch(url.pathname+url.search,init);
      }
      return nativeFetch(input,init);
    };
  }

  async function refreshGameProjectList(){
    if(!isGame||!currentProject()||typeof loadMine!=='function')return;
    await loadMine();
  }

  async function resolveGameProject(gameId){
    if(!isGame||!gameId)return currentProject();
    const id=encodeURIComponent(String(gameId));
    const previous=currentProject();
    const contextResponse=await nativeFetch(`/api/game-forge/games/${id}/project-context`,{credentials:'same-origin'});
    const context=await responseJson(contextResponse);
    if(context.creative_project_name){
      commitProject(context.creative_project_name);
      if(previous!==context.creative_project_name)await refreshGameProjectList();
      return context.creative_project_name;
    }
    const desired=currentProject();
    if(!desired)return '';
    const bindResponse=await nativeFetch(`/api/game-forge/games/${id}/project-context`,{
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({creative_project_name:desired}),
    });
    const bound=await responseJson(bindResponse);
    if(bound.creative_project_name){
      commitProject(bound.creative_project_name);
      if(previous!==bound.creative_project_name)await refreshGameProjectList();
    }
    return bound.creative_project_name||desired;
  }

  function wrapGameWorkspace(){
    if(!isGame||typeof openWorkspace!=='function')return;
    const baseOpen=openWorkspace;
    openWorkspace=async function(gameId,...args){
      try{await resolveGameProject(gameId)}catch(error){
        if(typeof notice==='function')notice(error.message||String(error),true);
        return;
      }
      return baseOpen(gameId,...args);
    };
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
      // Game Forge now persists this exact Creative DNA identity into Game DNA when a game is
      // created or an eligible legacy game is first opened from the project workspace. Refresh the
      // native list through the project-scoped transport so unrelated Game DNA never remains visible.
      contextualProject=requested;
      drawWorkspace();
      try{await refreshGameProjectList()}catch(_){/* Game Forge keeps its native list error handling. */}
      if(requestedGame&&typeof openWorkspace==='function'){
        try{await openWorkspace(requestedGame)}catch(_){/* The native Game Forge workspace reports errors. */}
      }
      return;
    }
    drawWorkspace();
  }

  installGameProjectTransport();
  wrapProjectFunctions();
  wrapGameWorkspace();
  drawWorkspace();
  void bootRequestedProject();

  document.addEventListener('change',event=>{
    if((isHouse&&event.target?.id==='projectName')||(isMedia&&event.target?.id==='project'))drawWorkspace();
  });

  window.CreativeProjectContinuity={
    currentProject,
    commitProject,
    projectHref,
    projectGameHref,
    projectGameExportHref,
    resolveGameProject,
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
