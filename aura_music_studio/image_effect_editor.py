from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .route_integrity import register_route_composition_hook

router = APIRouter(tags=["image-effects"])

_EDITOR_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Image Effect Studio</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:dark;background:#0b0d12;color:#f5f7fb}body{margin:0;padding:24px;max-width:1100px;margin-inline:auto}h1{margin:0 0 8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:#151923;border:1px solid #2b3240;border-radius:14px;padding:16px}label{display:block;margin:10px 0 5px}input,textarea,button{width:100%;box-sizing:border-box;border-radius:9px;border:1px solid #394255;background:#0f131b;color:#fff;padding:10px}textarea{min-height:100px;resize:vertical}button{cursor:pointer;background:#252d3d;margin-top:10px}button:disabled{opacity:.5;cursor:not-allowed}img{max-width:100%;border-radius:10px;background:#080a0f;min-height:180px;object-fit:contain}.status{white-space:pre-wrap;color:#c8d0df;min-height:42px}.nodes{font-family:ui-monospace,monospace;font-size:.9rem;white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>Image Effect Studio</h1>
<p>Compose a bounded local effect graph, preview it on a project image, then save the exact previewed graph as a private reusable preset.</p>
<div class="grid">
<section class="card">
<label for="effect-name">Effect name</label><input id="effect-name" value="Image FX">
<label for="prompt">Effect instructions</label><textarea id="prompt" maxlength="1200" placeholder="increase brightness and contrast"></textarea>
<button id="compose">Compose effect</button>
<label for="project">Project name</label><input id="project" autocomplete="off">
<label for="source">Project-relative source image</label><input id="source" placeholder="assets/photo.png" autocomplete="off">
<button id="preview" disabled>Preview effect</button>
<label for="preset">Private preset name</label><input id="preset" placeholder="portrait-bright">
<button id="save" disabled>Save exact preview as preset</button>
<div id="status" class="status" role="status" aria-live="polite"></div>
</section>
<section class="card">
<h2>Preview</h2><img id="preview-image" alt="Image effect preview">
<h2>Editable effect graph</h2><div id="nodes" class="nodes">Compose an effect to inspect its typed graph.</div>
</section>
</div>
<script>
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const state={graph:null,previewToken:null};
function status(message){$('status').textContent=message;}
function encodePath(value){return encodeURIComponent(value.trim());}
async function json(url,options={}){
  const response=await fetch(url,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
  let body={}; try{body=await response.json();}catch(_e){}
  if(!response.ok) throw new Error(body.detail||('Request failed ('+response.status+')'));
  return body;
}
$('compose').addEventListener('click',async()=>{
  state.graph=null; state.previewToken=null; $('preview').disabled=true; $('save').disabled=true; $('preview-image').removeAttribute('src');
  try{
    const body=await json('/image-effects/compose',{method:'POST',body:JSON.stringify({prompt:$('prompt').value,name:$('effect-name').value})});
    state.graph=body.graph; $('nodes').textContent=JSON.stringify(body.graph,null,2); $('preview').disabled=false; status('Effect graph composed. Preview it before saving.');
  }catch(error){status(error.message);}
});
$('preview').addEventListener('click',async()=>{
  if(!state.graph) return;
  const project=$('project').value.trim(), source=$('source').value.trim();
  if(!project||!source){status('Project name and project-relative source image are required.');return;}
  state.previewToken=null; $('save').disabled=true;
  try{
    const body=await json('/projects/'+encodePath(project)+'/image-effects/preview',{method:'POST',body:JSON.stringify({source,graph:state.graph})});
    state.previewToken=body.preview_token; $('preview-image').src=body.preview_url+'?v='+Date.now(); $('save').disabled=false; status('Preview rendered from the exact graph.');
  }catch(error){status(error.message);}
});
$('save').addEventListener('click',async()=>{
  const preset=$('preset').value.trim(); if(!state.graph||!state.previewToken||!preset){status('Preview the current graph and enter a preset name first.');return;}
  try{
    const body=await json('/image-effects/presets/'+encodePath(preset),{method:'POST',body:JSON.stringify({graph:state.graph,preview_token:state.previewToken})});
    status(body.saved?'Private preset saved.':'Preset was not saved.');
  }catch(error){status(error.message);}
});
})();
</script>
</body>
</html>'''


@router.get("/image-effects/editor", response_class=HTMLResponse)
def image_effect_editor() -> HTMLResponse:
    return HTMLResponse(
        _EDITOR_HTML,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


def _install_image_effect_editor_routes(app: Any) -> None:
    existing = {
        (str(getattr(route, "path", "")), tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)
    app.state.image_effect_editor_routes_installed = True


register_route_composition_hook("image_effect_editor_routes", _install_image_effect_editor_routes)

__all__ = ["router"]
