from __future__ import annotations

from typing import Any

from . import shared_sky_professional_canvas as canvas


TOOLBAR_CSS = r"""
.transport-console{border:1px solid var(--line);border-radius:10px;padding:10px;margin:0 0 12px;background:#06151d}
.transport-console h2{margin:.1rem 0 .45rem}.transport-badge{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.76rem;font-weight:900;text-transform:uppercase;letter-spacing:.04em}
.transport-badge.live{border-color:var(--green);color:var(--green)}.transport-badge.degraded,.transport-badge.reconnecting{border-color:var(--amber);color:var(--amber)}.transport-badge.failed,.transport-badge.stopping{border-color:var(--red);color:#ff9cab}.transport-badge.ready{border-color:var(--aqua);color:var(--aqua)}
.transport-actions{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.transport-actions button[disabled]{opacity:.45;cursor:not-allowed}.live-action{background:#184b35!important;border-color:#78e8a088!important}.stop-action{background:#4b1720!important;border-color:#ff687d88!important}.record-action{background:#3b2e12!important;border-color:#f6cf6788!important}
.transport-detail{font-size:.78rem;color:var(--muted);overflow-wrap:anywhere}.transport-alert{border-left:3px solid var(--amber);padding:5px 7px;margin:5px 0;background:#f6cf6710;font-size:.78rem}.transport-alert.blocker{border-color:var(--red);background:#ff687d10}.transport-ok{border-left:3px solid var(--green);padding:5px 7px;margin:5px 0;background:#78e8a010;font-size:.78rem}.destination-row,.marker-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;border-top:1px solid var(--line);padding:6px 0;font-size:.76rem}.marker-form{display:grid;grid-template-columns:72px 1fr;gap:6px;margin-top:6px}.marker-form input,.marker-form select{width:100%;background:#041016;border:1px solid var(--line);color:var(--text);padding:6px;border-radius:7px}.transport-subhead{font-weight:850;margin-top:8px;font-size:.82rem}.truth-chip{font-size:.7rem;color:var(--muted)}
"""


TOOLBAR_HTML = r"""
<section id='transportConsole' class='transport-console' aria-label='Broadcast transport controls'>
  <div class='row'><h2>Broadcast</h2><span id='transportBadge' class='transport-badge'>offline</span><span id='transportTruth' class='truth-chip'>authoritative state</span></div>
  <div id='broadcastIdentity' class='transport-detail'>No broadcast attached.</div>
  <div class='transport-actions'>
    <button id='attachBroadcast'>Attach broadcast</button>
    <button id='transportPreflight'>Preflight</button>
    <button id='transportStart' class='live-action'>Go Live</button>
    <button id='transportStop' class='stop-action'>Stop Live</button>
  </div>
  <div id='preflightState' aria-live='polite'></div>
  <div class='transport-subhead'>Destinations</div><div id='destinationState' class='transport-detail'>No destination state.</div>
  <div class='transport-subhead'>Recording</div>
  <div class='row'><select id='recordKind' aria-label='Recording kind'><option value='programme'>Programme</option><option value='clean_feed'>Clean feed</option><option value='isolated_source'>Isolated source</option><option value='audio_tracks'>Audio tracks</option></select><button id='recordStart' class='record-action'>Request recording</button></div>
  <div id='recordingState' class='transport-detail'>Recording state unavailable.</div>
  <div class='transport-subhead'>Markers</div>
  <div class='marker-form'><input id='markerSeconds' type='number' min='0' max='172800' step='.001' value='0' aria-label='Marker offset seconds'><select id='markerType' aria-label='Marker type'><option value='highlight'>Highlight</option><option value='chapter'>Chapter</option><option value='clip'>Clip</option><option value='replay'>Replay</option></select></div>
  <div class='row'><input id='markerLabel' maxlength='240' placeholder='Marker label' aria-label='Marker label'><button id='addMarker'>Add marker</button><button id='refreshMarkers'>Refresh</button></div>
  <div id='markerState' class='transport-detail'>No markers loaded.</div>
</section>
"""


TOOLBAR_JS = r"""
const transportUI={preflight:null,markers:[],busy:false};
function transportValue(){return String(state.transport?.state||'offline').toLowerCase();}
function activeTransport(){return ['starting','live','degraded','reconnecting','stopping'].includes(transportValue());}
function idem(prefix){const nonce=(globalThis.crypto?.randomUUID?.()||Math.random().toString(36).slice(2));return `${prefix}-${state.session?.id||'studio'}-${Date.now()}-${nonce}`.slice(0,190);}
function transportMessage(value){if(value===null||value===undefined)return'';if(typeof value==='string')return value;try{return JSON.stringify(value);}catch{return String(value);}}
function renderPreflight(){const host=$('#preflightState');if(!host)return;const p=transportUI.preflight;if(!p){host.innerHTML='<div class=transport-detail>Run authoritative preflight before Go Live.</div>';return;}const blockers=p.blocking_errors||[],warnings=p.warnings||[];if(p.ready)host.innerHTML=`<div class=transport-ok>Preflight ready.${warnings.length?` ${warnings.length} warning${warnings.length===1?'':'s'}.`:''}</div>`;else host.innerHTML=`<div class='transport-alert blocker'>Preflight blocked by ${blockers.length||1} issue${blockers.length===1?'':'s'}.</div>`;host.innerHTML+=blockers.map(x=>`<div class='transport-alert blocker'><b>${esc(x.scope||'transport')}</b> · ${esc(x.code||'blocked')} · ${esc(x.message||'Authoritative preflight blocker')}</div>`).join('')+warnings.map(x=>`<div class=transport-alert><b>${esc(x.scope||'transport')}</b> · ${esc(x.code||'warning')} · ${esc(x.message||'Transport warning')}</div>`).join('');}
function renderDestinations(){const host=$('#destinationState');if(!host)return;const rows=state.transport?.destinations||[];host.innerHTML=rows.length?rows.map(d=>{const s=String(d.state||d.capability_state||'unknown');const retry=['failed','reconnecting','unavailable'].includes(s)?`<button data-retry-destination='${esc(d.destination_id)}'>Retry</button>`:'';return `<div class=destination-row><span>${esc(d.destination_id||'destination')} · <b>${esc(s)}</b>${d.last_error_code?` · ${esc(d.last_error_code)}`:''}</span>${retry}</div>`}).join(''):'No configured destination runs.';$$('[data-retry-destination]').forEach(b=>b.onclick=()=>retryDestination(b.dataset.retryDestination));}
function renderRecordings(){const host=$('#recordingState');if(!host)return;const rows=state.transport?.recordings||[];host.innerHTML=rows.length?rows.map(r=>`${esc(r.kind||'recording')}: <b>${esc(r.state||'unknown')}</b>${r.asset_id?` · asset ${esc(r.asset_id)}`:''}`).join('<br>'):'No recording request is active. A request is not displayed as recording until Chat 2 reports its state.';}
function renderMarkers(){const host=$('#markerState');if(!host)return;host.innerHTML=transportUI.markers.length?transportUI.markers.slice(-10).reverse().map(m=>`<div class=marker-row><span><b>${esc(m.marker_type||'highlight')}</b> · ${(Number(m.offset_ms||0)/1000).toFixed(3)}s · ${esc(m.label||'')}</span><small>${esc(m.id||'')}</small></div>`).join(''):'No markers loaded.';}
function renderTransportPanel(){if(!state.session)return;const value=transportValue(),badge=$('#transportBadge');if(!badge)return;badge.textContent=value;badge.className=`transport-badge ${value}`;$('#broadcastIdentity').textContent=state.session.broadcast_id?`Broadcast ${state.session.broadcast_id}${state.transport?.source_id?` · programme source ${state.transport.source_id}`:''}${state.transport?.programme_source_bound?' · Studio programme bound':''}`:'No broadcast attached. Attach an existing Shared Sky broadcast for this project.';$('#transportTruth').textContent=state.transport?.authoritative===false?'non-authoritative':'authoritative state';const attached=Boolean(state.session.broadcast_id);$('#transportPreflight').disabled=!attached||transportUI.busy||value==='stopping';$('#transportStart').disabled=!attached||transportUI.busy||activeTransport();$('#transportStop').disabled=!attached||transportUI.busy||!['starting','live','degraded','reconnecting'].includes(value);$('#recordStart').disabled=!attached||transportUI.busy;$('#addMarker').disabled=!attached||transportUI.busy;$('#refreshMarkers').disabled=!attached||transportUI.busy;renderPreflight();renderDestinations();renderRecordings();renderMarkers();}
const baseStudioRender=render;render=function(){baseStudioRender();renderTransportPanel();};
async function refreshTransport(){if(!state.session)return;try{state.transport=await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/status`);renderTransportPanel();}catch(e){handle(e);}}
async function attachBroadcastUI(){const broadcastId=prompt('Existing Shared Sky broadcast ID for this project',state.session?.broadcast_id||'');if(!broadcastId)return;transportUI.busy=true;renderTransportPanel();try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/broadcast`,{method:'POST',body:JSON.stringify({broadcast_id:broadcastId.trim(),expected_studio_version:state.session.version})}));transportUI.preflight=null;transportUI.markers=[];await refreshTransport();await loadMarkersUI();$('#notice').textContent='Broadcast attached. Run authoritative preflight before Go Live.';}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function preflightTransportUI(){if(!state.session?.broadcast_id)return;transportUI.busy=true;renderTransportPanel();try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/preflight`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version})});transportUI.preflight=d.preflight||null;await refreshTransport();$('#notice').textContent=transportUI.preflight?.ready?'Authoritative transport preflight passed.':'Transport preflight is blocked; review the listed evidence.';return Boolean(transportUI.preflight?.ready);}catch(e){handle(e);return false;}finally{transportUI.busy=false;renderTransportPanel();}}
async function startTransportUI(){const ready=await preflightTransportUI();if(!ready)return;transportUI.busy=true;renderTransportPanel();try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/start`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version,idempotency_key:idem('studio-start')})});await refreshProject();await refreshTransport();$('#notice').textContent=`Transport reports ${transportValue()}. LIVE is shown only from authoritative Chat 2 state.`;}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function stopTransportUI(){if(!confirm('Stop the active Shared Sky broadcast and its delivery paths?'))return;transportUI.busy=true;renderTransportPanel();try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/stop`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version,idempotency_key:idem('studio-stop')})});await refreshProject();await refreshTransport();$('#notice').textContent=`Transport reports ${transportValue()} after stop.`;}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function retryDestination(id){transportUI.busy=true;renderTransportPanel();try{await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/retry-destination`,{method:'POST',body:JSON.stringify({expected_studio_version:state.session.version,idempotency_key:idem('studio-retry'),destination_id:id})});await refreshTransport();$('#notice').textContent=`Destination ${id} retry requested; current authoritative state is displayed.`;}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function requestRecordingUI(){transportUI.busy=true;renderTransportPanel();try{const kind=$('#recordKind').value;await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/recordings`,{method:'POST',body:JSON.stringify({kind})});await refreshTransport();$('#notice').textContent=`${kind} recording requested. The UI will not claim active recording until Chat 2 reports it.`;}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function addMarkerUI(){const seconds=Number($('#markerSeconds').value);if(!Number.isFinite(seconds)||seconds<0){handle(new Error('Marker offset must be zero or greater.'));return;}transportUI.busy=true;renderTransportPanel();try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/markers`,{method:'POST',body:JSON.stringify({offset_ms:Math.round(seconds*1000),label:$('#markerLabel').value||'',marker_type:$('#markerType').value})});if(d.marker)transportUI.markers.push(d.marker);$('#markerLabel').value='';renderMarkers();$('#notice').textContent='Authoritative recording marker saved.';}catch(e){handle(e);}finally{transportUI.busy=false;renderTransportPanel();}}
async function loadMarkersUI(){if(!state.session?.broadcast_id){transportUI.markers=[];renderMarkers();return;}try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/markers`);transportUI.markers=d.markers||[];renderMarkers();}catch(e){handle(e);}}
$('#attachBroadcast').onclick=attachBroadcastUI;$('#transportPreflight').onclick=preflightTransportUI;$('#transportStart').onclick=startTransportUI;$('#transportStop').onclick=stopTransportUI;$('#recordStart').onclick=requestRecordingUI;$('#addMarker').onclick=addMarkerUI;$('#refreshMarkers').onclick=loadMarkersUI;
setInterval(()=>{if(state.session?.broadcast_id&&!transportUI.busy)refreshTransport();},5000);
"""


def enhanced_professional_html(project_id: str, base_html) -> str:
    html = base_html(project_id)
    if "id='transportConsole'" in html:
        return html
    html = html.replace("</style>", TOOLBAR_CSS + "</style>", 1)
    html = html.replace(
        "<aside class='right panel'><h2>Inspector</h2>",
        "<aside class='right panel'>" + TOOLBAR_HTML + "<h2>Inspector</h2>",
        1,
    )
    html = html.replace("</script></body>", TOOLBAR_JS + "</script></body>", 1)
    return html


def install_professional_transport_toolbar(app: Any) -> None:
    del app  # The professional route is already mounted; replace its module-level renderer only.
    current = canvas.professional_html
    if getattr(current, "_shared_sky_transport_toolbar", False):
        return

    def wrapped(project_id: str) -> str:
        return enhanced_professional_html(project_id, current)

    setattr(wrapped, "_shared_sky_transport_toolbar", True)
    canvas.professional_html = wrapped


__all__ = [
    "TOOLBAR_CSS",
    "TOOLBAR_HTML",
    "TOOLBAR_JS",
    "enhanced_professional_html",
    "install_professional_transport_toolbar",
]
