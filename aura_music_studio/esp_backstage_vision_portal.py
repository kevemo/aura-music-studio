from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from .esp_backstage_evidence import backstage_evidence
from .esp_backstage_vision import router as vision_api_router, vision_runs
from .esp_niche import require_esp_hub_member

router = APIRouter()
router.include_router(vision_api_router)


def _agent(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership, role == "owner"


@router.get("/command-center/api/agent/backstage-evidence/{evidence_id}/image", include_in_schema=False)
def private_backstage_image(evidence_id: str, request: Request):
    member, _membership, owner = _agent(request)
    try:
        _public, row = vision_runs._source_row(evidence_id, member.user_id, owner=owner)
        path = vision_runs._safe_path(row)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Backstage evidence not found") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, no-store"})


CSS = """
:root{--line:#ffffff20;--muted:#c7bfd2;--gold:#efc66b;--violet:#a16eff;--green:#78dda5;--red:#ff91a5}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.split{display:grid;grid-template-columns:minmax(260px,.8fr) 1.2fr;gap:14px}.shot{width:100%;max-height:520px;object-fit:contain;border:1px solid var(--line);border-radius:14px;background:#05040a}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.confirm{background:#174b33;border-color:#2c8b61}.reject{background:#5a2030;border-color:#98445c}textarea{width:100%;min-height:190px;border:1px solid var(--line);border-radius:11px;background:#08060d;color:#fff;padding:11px;font-family:ui-monospace,monospace}.status{margin:8px 0;color:var(--muted)}@media(max-width:780px){.split{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const state={};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
async function analyse(id){const s=document.getElementById('status-'+id), box=document.getElementById('metrics-'+id), actions=document.getElementById('actions-'+id);s.textContent='Aura is analysing visible screenshot metrics…';try{const d=await req(`/command-center/api/agent/backstage-evidence/${id}/vision`,{method:'POST'});state[id]={run:d.run_id};box.value=JSON.stringify(d.proposed_metrics||{},null,2);actions.style.display='block';s.textContent=`Vision proposal ready. Confidence: ${d.confidence??'not reported'}. Check every value against the screenshot before confirming.`}catch(e){s.textContent=e.message}}
async function decide(id,confirm){const s=document.getElementById('status-'+id), box=document.getElementById('metrics-'+id);if(!state[id]?.run){s.textContent='Run Aura analysis first.';return}let metrics={};if(confirm){try{metrics=JSON.parse(box.value||'{}')}catch(_){s.textContent='Metrics JSON is invalid. Correct it before confirming.';return}}try{const d=await req(`/command-center/api/agent/backstage-vision/${state[id].run}/confirm`,{method:'POST',body:JSON.stringify({confirm,metrics,reviewer_note:confirm?'Agent/owner visually confirmed the screenshot metrics in the review portal.':'Agent/owner rejected the proposed screenshot extraction.'})});s.textContent=confirm?'Confirmed. These reviewed metrics are now in creator progress.':'Rejected. No creator progress data was added.';document.getElementById('actions-'+id).style.display='none'}catch(e){s.textContent=e.message}}
"""


@router.get("/command-center/agent/backstage-vision", response_class=HTMLResponse, include_in_schema=False)
def backstage_vision_page(request: Request):
    member, _membership, owner = _agent(request)
    queue = backstage_evidence.queue(member.user_id, owner=owner)
    rows = []
    for creator in queue:
        latest = creator.get("latest") or {}
        upload_name = str(latest.get("upload_name") or "")
        if latest.get("source_kind") != "screenshot" or not upload_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        evidence_id = str(latest.get("id") or "")
        if not evidence_id:
            continue
        rows.append(
            "<article class='card'>"
            f"<div class='row'><div><div class='eyebrow'>{escape(creator.get('region') or 'ESP Creator')}</div>"
            f"<h2>{escape(creator.get('display_name') or 'Creator')}</h2><div class='muted'>@{escape(creator.get('tiktok_handle') or '')} · {escape(latest.get('period_label') or 'Screenshot evidence')}</div></div>"
            f"<div class='muted'>{escape(str(latest.get('extraction_status') or 'visual review required').replace('_',' ').title())}</div></div>"
            "<div class='split'><div>"
            f"<img class='shot' src='/command-center/api/agent/backstage-evidence/{escape(evidence_id, quote=True)}/image' alt='Private authorised creator analytics screenshot'>"
            "</div><div>"
            f"<button class='primary' onclick=\"analyse('{escape(evidence_id, quote=True)}')\">Analyse with Aura</button>"
            f"<div id='status-{escape(evidence_id)}' class='status'>No automated values are trusted until you compare them with the screenshot.</div>"
            f"<textarea id='metrics-{escape(evidence_id)}' aria-label='Extracted metrics' placeholder='Aura-proposed metrics will appear here. You can correct values before confirming.'></textarea>"
            f"<div id='actions-{escape(evidence_id)}' style='display:none;margin-top:8px'><button class='confirm' onclick=\"decide('{escape(evidence_id, quote=True)}',true)\">Confirm visible metrics</button> <button class='reject' onclick=\"decide('{escape(evidence_id, quote=True)}',false)\">Reject extraction</button></div>"
            "</div></div></article>"
        )
    cards = "".join(rows) or "<div class='card muted'>No latest screenshot evidence is waiting in your assigned creator queue. Upload evidence from Backstage Evidence & Analysis first.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>ESP Backstage Screenshot Vision</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div>"
        "<div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Screenshot Data Review</h1>"
        "<p class='muted'>Aura extracts only values visible in authorised creator screenshots. Agents/owners review and can edit every proposed metric before confirmation. This is not a direct TikTok LIVE Backstage connection.</p></div>"
        "<div><a class='btn' href='/command-center/agent/backstage-evidence'>Backstage Evidence</a><a class='btn primary' href='/command-center/agent/development'>Development Planner</a></div></div>"
        f"{cards}</main><script>{SCRIPT}</script></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
