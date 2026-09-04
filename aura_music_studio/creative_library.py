from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .commercial_entitlements import can_download_media
from .creative_project import CreativeProjectStore
from .tenant_storage import list_project_dirs

router = APIRouter(prefix="/creative", tags=["creative-library"])

_MEDIA_KINDS = {"image", "video", "audio", "music"}
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif",
    ".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi",
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg",
}


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _valid_local_media(project: Path, source_ref: str) -> bool:
    clean = str(source_ref or "").strip()
    if not clean:
        return False
    relative = Path(clean)
    if relative.is_absolute() or relative.suffix.lower() not in _MEDIA_SUFFIXES:
        return False
    root = project.resolve()
    target = (root / relative).resolve()
    return root in target.parents and target.is_file()


def scan_creative_library(member, project_dirs: list[Path] | None = None) -> list[dict]:
    """Return playable media metadata for the current member's Creative DNA projects.

    The response intentionally contains element-based application URLs rather than local
    filesystem paths. The media endpoint re-authorizes every request and applies download
    entitlements independently of this catalog.
    """
    items: list[dict] = []
    for project in project_dirs if project_dirs is not None else list_project_dirs():
        try:
            manifest = CreativeProjectStore(project).load()
        except Exception:
            continue
        active_ids = set(getattr(manifest, "active_element_ids", []) or [])
        project_title = str(getattr(manifest, "title", "") or project.name)
        for element in getattr(manifest, "elements", []) or []:
            kind = str(getattr(element, "kind", "") or "")
            source_ref = str(getattr(element, "source_ref", "") or "")
            if kind not in _MEDIA_KINDS or not _valid_local_media(project, source_ref):
                continue
            element_id = str(getattr(element, "id", ""))
            if not element_id:
                continue
            media_url = f"/creative/projects/{project.name}/elements/{element_id}/media"
            item = {
                "id": f"{project.name}:{element_id}",
                "element_id": element_id,
                "project": project.name,
                "project_title": project_title,
                "title": str(getattr(element, "label", "") or kind.title()),
                "kind": kind,
                "role": str(getattr(element, "role", "") or ""),
                "status": str(getattr(element, "status", "") or ""),
                "current": element_id in active_ids,
                "media_url": media_url,
                "download_url": media_url + "?download=true" if can_download_media(member, kind) else None,
                "download_allowed": can_download_media(member, kind),
                "download_reason": (
                    "Included in this membership tier"
                    if can_download_media(member, kind)
                    else "Music/video downloads require Basic (£4.99) or Pro (£9.99)"
                ),
            }
            items.append(item)
    items.sort(key=lambda row: (row["project_title"].casefold(), 0 if row["current"] else 1, row["title"].casefold()))
    return items


@router.get("/api/library")
def creative_library_api(request: Request, kind: str = "", project: str = "", q: str = ""):
    member = _member(request)
    rows = scan_creative_library(member)
    kind = kind.strip().lower()
    project = project.strip()
    query = q.strip().casefold()
    if kind:
        if kind not in _MEDIA_KINDS:
            raise HTTPException(400, "kind must be image, video, audio or music")
        rows = [row for row in rows if row["kind"] == kind]
    if project:
        rows = [row for row in rows if row["project"] == project]
    if query:
        rows = [row for row in rows if query in " ".join((row["title"], row["project_title"], row["role"], row["kind"])).casefold()]
    return {
        "items": rows,
        "count": len(rows),
        "plan": member.plan.id,
        "private_member_library": True,
        "filesystem_paths_exposed": False,
    }


@router.get("/library", response_class=HTMLResponse, include_in_schema=False)
def creative_library_page(request: Request):
    member = _member(request)
    rows = scan_creative_library(member)
    plan = str(member.plan.id).title()
    cards: list[str] = []
    for item in rows:
        media = ""
        if item["kind"] == "image":
            media = f"<img loading='lazy' src='{escape(item['media_url'], quote=True)}' alt='{escape(item['title'], quote=True)}'>"
        elif item["kind"] == "video":
            media = "<div class='mediaIcon'>🎬</div>"
        else:
            media = "<div class='mediaIcon'>🎵</div>"
        play = ""
        if item["kind"] != "image":
            play = (
                f"<button class='btn primary' data-pulsar-play='{escape(item['media_url'], quote=True)}' "
                f"data-pulsar-id='{escape(item['id'], quote=True)}' data-pulsar-kind='{escape(item['kind'], quote=True)}' "
                f"data-pulsar-title='{escape(item['title'], quote=True)}' data-pulsar-project='{escape(item['project_title'], quote=True)}' "
                f"data-pulsar-element='{escape(item['element_id'], quote=True)}'>▶ Play</button>"
            )
        download = (
            f"<a class='btn' href='{escape(str(item['download_url']), quote=True)}'>Download</a>"
            if item["download_url"]
            else f"<span class='locked'>{escape(item['download_reason'])}</span>"
        )
        cards.append(
            "<article class='card' "
            f"data-kind='{escape(item['kind'], quote=True)}' data-search='{escape((item['title']+' '+item['project_title']+' '+item['role']).casefold(), quote=True)}'>"
            f"<div class='visual'>{media}</div><div class='top'><span class='pill'>{escape(item['kind'].upper())}</span>"
            f"<span class='pill {'good' if item['current'] else ''}'>{'CURRENT' if item['current'] else 'HISTORY'}</span></div>"
            f"<h3>{escape(item['title'])}</h3><p>{escape(item['project_title'])}</p><small>{escape(item['role'] or 'Creative output')}</small>"
            f"<div class='actions'>{play}<a class='btn' href='{escape(item['media_url'], quote=True)}' target='_blank' rel='noopener'>Preview</a>{download}</div></article>"
        )
    listing = "".join(cards) if cards else "<div class='empty'>No playable Creative DNA outputs yet. Create or import media in a project and it will appear here automatically.</div>"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Creative Library — Pulsar-Frequency House</title><style>
    :root{{--line:#ffffff1e;--gold:#f2c86f;--violet:#9f70ff;--cyan:#5ce8ff;--muted:#c0bacb;--good:#79dfa6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#43165e,transparent 31%),radial-gradient(circle at 92% 0,#123f59,transparent 28%),#05040a;color:#fff;font-family:Inter,system-ui,sans-serif;padding-bottom:110px}}a{{color:inherit;text-decoration:none}}button,input,select{{font:inherit}}.wrap{{width:min(1440px,calc(100% - 28px));margin:auto;padding:34px 0 70px}}.top,.actions,.filters{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.top{{justify-content:space-between}}.eyebrow{{color:var(--gold);font-size:.7rem;text-transform:uppercase;letter-spacing:.15em;font-weight:950}}h1{{font-size:clamp(3rem,7vw,5.8rem);letter-spacing:-.06em;line-height:.93;margin:.12em 0}}p,.muted,small{{color:var(--muted);line-height:1.5}}.btn,input,select{{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff}}.btn{{font-weight:850;cursor:pointer}}.btn.primary{{border:0;background:linear-gradient(110deg,#f4dfa0,var(--gold),var(--violet));color:#160b1c}}.filters{{margin:18px 0}}.filters input{{flex:1;min-width:220px}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#15101fea,#090711ee);padding:12px;min-width:0}}.visual{{height:150px;border-radius:13px;overflow:hidden;background:radial-gradient(circle,#3b2057,#090a12);display:grid;place-items:center;margin-bottom:10px}}.visual img{{width:100%;height:100%;object-fit:cover}}.mediaIcon{{font-size:3.1rem}}.card h3{{margin:9px 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.card p{{margin:0 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.62rem;margin-right:4px}}.pill.good{{color:var(--good);border-color:#79dfa64c}}.actions{{margin-top:11px}}.locked{{font-size:.67rem;color:#d9b678;line-height:1.25}}.empty{{border:1px dashed #ffffff2a;border-radius:18px;padding:28px;text-align:center;color:var(--muted)}}@media(max-width:1100px){{.grid{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Unified Creative Library · {escape(plan)} plan</div><h1>Your creations.</h1><p>Play music and videos in the persistent Pulsar Player, preview images, return to your Creative House, and download only where your current membership allows.</p></div><div class='actions'><a class='btn' href='/creative/effects-library'>Effects Library</a><a class='btn' href='/creative-house'>Creative House</a><a class='btn' href='/dashboard'>Dashboard</a></div></div><section class='filters'><input id='librarySearch' placeholder='Search title, project or role'><select id='libraryKind'><option value=''>All media</option><option value='music'>Music</option><option value='audio'>Audio</option><option value='video'>Video</option><option value='image'>Images</option></select><button class='btn' id='libraryClear'>Clear</button></section><section class='grid' id='libraryGrid'>{listing}</section></main><script>
    (()=>{{const q=document.getElementById('librarySearch'),kind=document.getElementById('libraryKind'),cards=[...document.querySelectorAll('#libraryGrid .card')];function filter(){{const text=q.value.trim().toLowerCase(),k=kind.value;for(const card of cards){{card.style.display=(!k||card.dataset.kind===k)&&(!text||(card.dataset.search||'').includes(text))?'block':'none'}}}}q.addEventListener('input',filter);kind.addEventListener('change',filter);document.getElementById('libraryClear').onclick=()=>{{q.value='';kind.value='';filter()}}}})();
    </script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


_EFFECTS_LIBRARY_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Universal Effects Library — Elevate Souls Productions</title>
<style>
:root{--bg:#05040a;--panel:#11101b;--line:#ffffff1d;--gold:#f2c86f;--silver:#d7deeb;--violet:#9f70ff;--cyan:#5ce8ff;--muted:#beb8ca;--good:#7de2aa;--warn:#ffd27a;--bad:#ff95aa}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#43165e,transparent 31%),radial-gradient(circle at 92% 0,#123f59,transparent 28%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif;min-height:100vh}a{color:inherit;text-decoration:none}button,input,select{font:inherit}.wrap{width:min(1500px,calc(100% - 26px));margin:auto;padding:28px 0 80px}.top,.actions,.filters,.stats,.badges{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.top{justify-content:space-between}.eyebrow{color:var(--gold);font-weight:950;font-size:.69rem;letter-spacing:.14em;text-transform:uppercase}h1{font-size:clamp(2.8rem,7vw,5.8rem);letter-spacing:-.06em;line-height:.92;margin:.14em 0 .2em}.lead,.muted,small{color:var(--muted);line-height:1.55}.btn,input,select{border:1px solid var(--line);border-radius:11px;padding:10px 12px;background:#ffffff08;color:#fff}.btn{cursor:pointer;font-weight:850}.btn.primary{border:0;background:linear-gradient(110deg,#fff0ae,var(--gold),var(--violet));color:#150a1c}.btn:disabled{opacity:.45;cursor:not-allowed}.filters{position:sticky;top:0;z-index:4;margin:20px 0 12px;padding:12px;border:1px solid var(--line);border-radius:16px;background:#090812ef;backdrop-filter:blur(14px)}.filters input{flex:1;min-width:210px}.check{display:flex;gap:7px;align-items:center;border:1px solid var(--line);border-radius:11px;padding:9px 11px;color:var(--muted)}.check input{width:auto;padding:0}.stats{margin:9px 0 14px}.stat,.pill{border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:.7rem}.pill.core{color:var(--good)}.pill.silver{color:var(--silver)}.pill.gold{color:var(--gold)}.pill.owned{color:var(--good);border-color:#7de2aa55}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card{border:1px solid var(--line);border-radius:18px;padding:14px;background:linear-gradient(145deg,#15101fea,#090711ef);min-width:0}.card h3{margin:10px 0 4px;line-height:1.15}.card p{margin:0 0 7px}.meta{font:600 .68rem ui-monospace,SFMono-Regular,Menlo,monospace;color:#9290a0;word-break:break-all}.description{min-height:3.1em}.status{margin-top:8px;padding:8px 9px;border:1px solid var(--line);border-radius:10px;font-size:.72rem;color:var(--muted)}.status.exec{color:var(--good)}.status.pending{color:var(--warn)}.actions{margin-top:11px}.empty,.notice{border:1px dashed #ffffff2c;border-radius:16px;padding:22px;color:var(--muted)}.notice{display:none;margin:10px 0;border-style:solid}.notice.show{display:block}.notice.error{color:#ffd6de;border-color:#ff95aa55}.notice.good{color:var(--good);border-color:#7de2aa55}.detail{margin-top:8px;white-space:pre-wrap;font-size:.68rem;color:var(--muted);max-height:140px;overflow:auto}.skeleton{opacity:.65}.price{font-weight:950}.price.silver{color:var(--silver)}.price.gold{color:var(--gold)}@media(max-width:1120px){.grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.grid{grid-template-columns:1fr}.filters{position:static}.filters>*{width:100%}.check{width:100%}}
</style></head>
<body><main class="wrap"><header class="top"><div><div class="eyebrow">Universal Creative Effects Library · __PLAN__ plan</div><h1>Find it. Own it. Create with it.</h1><p class="lead">Browse original ESP/Aura effect capabilities across the studios. Core effects are included. Silver and Gold first-party effects can be permanently unlocked with Cosmic Creation Coins. Catalogue entries remain truth-labelled until their real runtime exists.</p></div><div class="actions"><a class="btn" href="/creative/library">Your Creations</a><a class="btn" href="/creative-house">Creative House</a><a class="btn" href="/dashboard">Dashboard</a></div></header>
<div id="notice" class="notice" role="status" aria-live="polite"></div>
<section class="filters" aria-label="Effect filters"><input id="search" type="search" placeholder="Search effects, systems, categories or tags" autocomplete="off"><select id="studio"><option value="">All studios</option></select><select id="band"><option value="">All access</option><option value="core">Core / Free</option><option value="silver">Silver · 200 CCC</option><option value="gold">Gold · 500 CCC</option></select><label class="check"><input id="owned" type="checkbox"> Owned / included</label><button id="refresh" class="btn">Refresh</button></section>
<section class="stats"><span id="count" class="stat">Loading…</span><span class="stat">Core · Included</span><span class="stat">Silver · 200 CCC</span><span class="stat">Gold · 500 CCC</span><span class="stat">Purchases are permanent account unlocks</span></section>
<section id="grid" class="grid"><div class="empty">Loading the executable creative catalogue…</div></section></main>
<script>
(()=>{'use strict';const API='/command-center/api/universal-library';const q=id=>document.getElementById(id);let rows=[];let loadToken=0;
function text(node,value){node.textContent=value==null?'':String(value);return node}function notify(message,kind=''){const n=q('notice');text(n,message);n.className='notice show '+kind;clearTimeout(window.__effectNotice);window.__effectNotice=setTimeout(()=>n.className='notice',6500)}
async function api(path,opt={}){const response=await fetch(API+path,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let body={};try{body=await response.json()}catch(_e){}if(!response.ok)throw new Error(body.detail||`Request failed (${response.status})`);return body}
function bandLabel(row){const band=String(row.entitlement||'core').toLowerCase();if(band==='core')return 'Core · Included';const value=Number(row.ccc_price||0);return `${band==='gold'?'Gold':'Silver'} · ${value} CCC`}
function addPill(parent,label,cls=''){const span=document.createElement('span');span.className='pill '+cls;text(span,label);parent.appendChild(span)}
function actionButton(label,handler,cls='btn'){const button=document.createElement('button');button.type='button';button.className=cls;text(button,label);button.addEventListener('click',handler);return button}
function card(row){const article=document.createElement('article');article.className='card';const badges=document.createElement('div');badges.className='badges';const band=String(row.entitlement||'core').toLowerCase();addPill(badges,bandLabel(row),band);if(row.owned)addPill(badges,'Owned','owned');article.appendChild(badges);const h=document.createElement('h3');text(h,row.label||row.name||row.id);article.appendChild(h);const meta=document.createElement('div');meta.className='meta';text(meta,`${row.studio||'creative'} · ${row.category||'effect'} · ${row.id||''}`);article.appendChild(meta);const p=document.createElement('p');p.className='description muted';text(p,row.description||'Original ESP/Aura creative capability.');article.appendChild(p);const runtime=document.createElement('div');runtime.className='status '+(row.backend_executable?'exec':'pending');text(runtime,row.backend_executable?`Executable backend · ${row.runtime||'runtime'}`:`${row.status||'Catalogue contract'} · runtime not yet executable`);article.appendChild(runtime);const detail=document.createElement('div');detail.className='detail';text(detail,`${Object.keys(row.parameters||{}).length} editable parameter${Object.keys(row.parameters||{}).length===1?'':'s'} · ${Array.isArray(row.tags)?row.tags.join(' · '):''}`);article.appendChild(detail);const actions=document.createElement('div');actions.className='actions';
if(row.preview_compile_available){actions.appendChild(actionButton('Preview plan',()=>preview(row)))}
if(!row.owned&&band!=='core'){const price=Number(row.ccc_price||0);const buy=actionButton(`Unlock · ${price} CCC`,()=>purchase(row,buy),'btn primary');actions.appendChild(buy)}
if(!row.preview_compile_available&&row.owned){const note=document.createElement('span');note.className='muted';text(note,row.backend_executable?'Owned — open the compatible studio to use this capability.':'Owned — runtime implementation is still pending.');actions.appendChild(note)}
article.appendChild(actions);return article}
function render(){const grid=q('grid');grid.replaceChildren();const ownedOnly=q('owned').checked;const filtered=ownedOnly?rows.filter(row=>row.owned):rows;q('count').textContent=`${filtered.length} effect${filtered.length===1?'':'s'} shown`;if(!filtered.length){const empty=document.createElement('div');empty.className='empty';text(empty,'No effects match these filters.');grid.appendChild(empty);return}for(const row of filtered)grid.appendChild(card(row))}
function rebuildStudios(){const select=q('studio');const current=select.value;const studios=[...new Set(rows.map(row=>String(row.studio||'').trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b));while(select.options.length>1)select.remove(1);for(const studio of studios){const option=document.createElement('option');option.value=studio;text(option,studio.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase()));select.appendChild(option)}if(studios.includes(current))select.value=current}
async function load(){const token=++loadToken;const params=new URLSearchParams();const query=q('search').value.trim();const studio=q('studio').value;const band=q('band').value;if(query)params.set('q',query);if(studio)params.set('studio',studio);if(band)params.set('entitlement',band);q('count').textContent='Loading…';try{const body=await api('/runtime-effects'+(params.size?'?'+params.toString():''));if(token!==loadToken)return;rows=Array.isArray(body.items)?body.items:[];rebuildStudios();render()}catch(error){if(token!==loadToken)return;rows=[];render();notify(error.message,'error')}}
async function purchase(row,button){button.disabled=true;const id=String(row.id||'');const key=(globalThis.crypto&&typeof crypto.randomUUID==='function')?crypto.randomUUID():`effect-${Date.now()}-${Math.random().toString(16).slice(2)}`;try{await api(`/runtime-effects/${encodeURIComponent(id)}/purchase`,{method:'POST',body:JSON.stringify({idempotency_key:key})});notify(`${row.label||id} is now in My Effects.`,'good');await load()}catch(error){notify(error.message,'error');button.disabled=false}}
async function preview(row){try{const body=await api(`/runtime-effects/${encodeURIComponent(String(row.id||''))}/preview-plan`,{method:'POST',body:JSON.stringify({parameters:{},enabled:true,mix:1})});notify(`Preview plan compiled for ${row.label||row.id}. No project media was changed.`,'good');const summary=body.filter_chain?`Compiled runtime: ${body.runtime}. Filter chain ready for a compatible studio preview.`:`Compiled runtime: ${body.runtime}.`;const match=[...q('grid').children].find(node=>node.querySelector&&node.querySelector('.meta')?.textContent.includes(row.id));if(match){const detail=match.querySelector('.detail');if(detail)text(detail,summary)}}catch(error){notify(error.message,'error')}}
let debounce=0;q('search').addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(load,220)});q('studio').addEventListener('change',load);q('band').addEventListener('change',load);q('owned').addEventListener('change',render);q('refresh').addEventListener('click',load);load();})();
</script></body></html>"""


@router.get("/effects-library", response_class=HTMLResponse, include_in_schema=False)
def creative_effects_library_page(request: Request):
    """Authenticated browser for the authoritative Universal Creative Catalogue.

    Prices and ownership are deliberately loaded from server-authoritative APIs. The page
    never exposes a fake generic Apply action: effects are only previewable where the
    catalogue reports a real preview compiler, and project mutation remains owned by the
    compatible studio runtime.
    """
    member = _member(request)
    plan = escape(str(member.plan.id).title())
    html = _EFFECTS_LIBRARY_HTML.replace("__PLAN__", plan)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "same-origin",
        },
    )


__all__ = ["creative_effects_library_page", "router", "scan_creative_library"]
