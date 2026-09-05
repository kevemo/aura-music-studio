from __future__ import annotations

from typing import Any

from . import shared_sky_professional_canvas as canvas


INGEST_CSS = r"""
.ingest-secret{border:1px solid var(--amber);background:#f6cf670d;border-radius:8px;padding:7px;margin:6px 0}.ingest-secret textarea{width:100%;min-height:74px;resize:vertical;background:#02090d;border:1px solid var(--line);color:var(--text);padding:6px;border-radius:6px;font:12px ui-monospace,monospace}.ingest-state{font-size:.76rem;color:var(--muted);overflow-wrap:anywhere}.ingest-warning{font-size:.72rem;color:var(--amber);margin-top:4px}
"""

INGEST_HTML = r"""
  <div class='transport-subhead'>Contribution ingest</div>
  <div id='studioIngestState' class='ingest-state'>Signed ingest status not loaded.</div>
  <div class='transport-actions'><button id='studioIngestIssue'>Issue / rotate credential</button><button id='studioIngestRevoke' class='stop-action'>Revoke credential</button></div>
  <div id='studioIngestSecret' class='ingest-secret' hidden>
    <div><b>One-time ingest URL</b> · copy it now. Reloading this page will not recover it.</div>
    <textarea id='studioIngestUrl' readonly aria-label='One-time signed ingest URL'></textarea>
    <div class='transport-actions'><button id='studioIngestCopy'>Copy URL</button><button id='studioIngestHide'>Hide secret</button></div>
  </div>
  <div class='ingest-warning'>Credential issuance proves only the signed control-plane handoff. It does not prove an RTMP/SRT/WebRTC termination service is deployed.</div>
"""

INGEST_JS = r"""
const studioIngestUI={status:null,secret:null,busy:false};
function renderStudioIngest(){const host=$('#studioIngestState');if(!host||!state.session)return;const attached=Boolean(state.session.broadcast_id),active=activeTransport();if(studioIngestUI.secret&&studioIngestUI.secret.broadcast_id!==state.session.broadcast_id)studioIngestUI.secret=null;const s=studioIngestUI.status,meta=s?.session,plane=s?.media_plane||{};host.textContent=!attached?'Attach a broadcast before managing signed ingest.':meta?`Session ${meta.id} · ${meta.state||'unknown'} · expires ${meta.expires_at||'unknown'}${meta.node_id?` · node ${meta.node_id}`:''}`:`No signed ingest session attached. Signing ${plane.signing_configured?'configured':'unavailable'} · healthy nodes ${Number(plane.healthy_nodes||0)}.`;$('#studioIngestIssue').disabled=!attached||active||studioIngestUI.busy;$('#studioIngestRevoke').disabled=!attached||active||studioIngestUI.busy||!meta;const secret=$('#studioIngestSecret');if(studioIngestUI.secret){secret.hidden=false;$('#studioIngestUrl').value=studioIngestUI.secret.ingest_url||'';}else{secret.hidden=true;$('#studioIngestUrl').value='';}}
async function loadStudioIngestStatus(){if(!state.session?.broadcast_id){studioIngestUI.status=null;renderStudioIngest();return;}try{studioIngestUI.status=await api(`/shared-sky/studio/api/sessions/${state.session.id}/ingest`);renderStudioIngest();}catch(e){handle(e);}}
async function issueStudioIngest(){if(activeTransport()){handle(new Error('Stop transport before rotating signed ingest credentials.'));return;}if(!confirm('Issue a new short-lived signed contribution-ingest credential? Any previously attached credential will be revoked after the new one is bound.'))return;studioIngestUI.busy=true;renderStudioIngest();try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/ingest`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version,ttl_seconds:900})});studioIngestUI.secret=d.session||null;await loadStudioIngestStatus();$('#notice').textContent='Signed ingest credential issued. Copy the one-time URL now; Studio will not recover the secret after reload.';}catch(e){handle(e);}finally{studioIngestUI.busy=false;renderStudioIngest();}}
async function revokeStudioIngest(){if(activeTransport()){handle(new Error('Stop transport before revoking signed ingest credentials.'));return;}if(!confirm('Revoke the attached signed ingest credential? This cannot be undone.'))return;studioIngestUI.busy=true;renderStudioIngest();try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/ingest/revoke`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version})});studioIngestUI.secret=null;await loadStudioIngestStatus();$('#notice').textContent='Signed ingest credential revoked and detached from transport.';}catch(e){handle(e);}finally{studioIngestUI.busy=false;renderStudioIngest();}}
async function copyStudioIngest(){const value=studioIngestUI.secret?.ingest_url||'';if(!value)return;try{await navigator.clipboard.writeText(value);$('#notice').textContent='One-time ingest URL copied to clipboard.';}catch{handle(new Error('Clipboard access was denied. Select and copy the URL manually.'));}}
function hideStudioIngest(){studioIngestUI.secret=null;renderStudioIngest();$('#notice').textContent='The one-time ingest secret has been hidden from this page and cannot be recovered by Studio.';}
const baseStudioIngestRenderTransport=renderTransportPanel;renderTransportPanel=function(){baseStudioIngestRenderTransport();renderStudioIngest();};
const baseStudioIngestRefreshTransport=refreshTransport;refreshTransport=async function(){await baseStudioIngestRefreshTransport();await loadStudioIngestStatus();};
$('#studioIngestIssue').onclick=issueStudioIngest;$('#studioIngestRevoke').onclick=revokeStudioIngest;$('#studioIngestCopy').onclick=copyStudioIngest;$('#studioIngestHide').onclick=hideStudioIngest;
setInterval(()=>{if(state.session?.broadcast_id&&!studioIngestUI.busy&&!transportUI.busy)loadStudioIngestStatus();},10000);
"""


def enhanced_ingest_html(project_id: str, base_html) -> str:
    html = base_html(project_id)
    if "id='studioIngestState'" in html:
        return html
    html = html.replace("</style>", INGEST_CSS + "</style>", 1)
    html = html.replace(
        "<div class='transport-subhead'>Destinations</div>",
        INGEST_HTML + "<div class='transport-subhead'>Destinations</div>",
        1,
    )
    html = html.replace("</script></body>", INGEST_JS + "</script></body>", 1)
    return html


def install_professional_ingest_ui(app: Any) -> None:
    del app
    current = canvas.professional_html
    if getattr(current, "_shared_sky_ingest_ui", False):
        return

    def wrapped(project_id: str) -> str:
        return enhanced_ingest_html(project_id, current)

    for marker in (
        "_shared_sky_transport_toolbar",
        "_shared_sky_operator_ui",
        "_shared_sky_motion_graphics_ui",
    ):
        if getattr(current, marker, False):
            setattr(wrapped, marker, True)
    setattr(wrapped, "_shared_sky_ingest_ui", True)
    canvas.professional_html = wrapped


__all__ = [
    "INGEST_CSS",
    "INGEST_HTML",
    "INGEST_JS",
    "enhanced_ingest_html",
    "install_professional_ingest_ui",
]
