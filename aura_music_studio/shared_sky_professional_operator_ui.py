from __future__ import annotations

from typing import Any

from . import shared_sky_professional_canvas as canvas


OPERATOR_CSS = r"""
.operator-console{border:1px solid var(--line);border-radius:10px;padding:10px;margin:0 0 12px;background:#081922}.operator-console h2{margin:.1rem 0 .45rem}.operator-profile-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.operator-profile-row select{max-width:180px;background:#041016;border:1px solid var(--line);color:var(--text);padding:6px;border-radius:7px}.hotkey-grid{display:grid;grid-template-columns:minmax(100px,1fr) minmax(95px,1fr);gap:4px;font-size:.75rem;margin-top:6px}.hotkey-grid code{color:var(--aqua)}.macro-buttons{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}.macro-buttons button{font-size:.76rem}.operator-warning{font-size:.72rem;color:var(--muted);margin-top:6px}
"""


OPERATOR_HTML = r"""
<section id='operatorConsole' class='operator-console' aria-label='Operator hotkey and macro profile'>
  <h2>Operator Profile</h2>
  <div class='operator-profile-row'><select id='operatorProfile' aria-label='Operator profile'><option value=''>Default fixed controls</option></select><button id='activateOperatorProfile'>Activate selected</button><button id='createOperatorProfile'>New</button></div>
  <div class='operator-profile-row'><button id='addOperatorHotkey'>Add hotkey</button><button id='addOperatorMacro'>Add macro</button><button id='deleteOperatorProfile' class='danger'>Delete selected</button></div>
  <div id='operatorHotkeys' class='hotkey-grid'></div>
  <div id='operatorMacros' class='macro-buttons'></div>
  <div class='operator-warning'>Hotkeys and macros edit the active profile. Delete works only for an inactive selected profile. Macros are explicit operator actions. Transport, recording, participant and destination mutations are not valid macro commands. Programme-changing macros confirm every run.</div>
</section>
"""


OPERATOR_JS = r"""
const operatorUI={profiles:[],active:null,busy:false};
const OPERATOR_COMMANDS=['cut','transition','undo','redo','scene_next','scene_previous','marker_highlight'];
function activeOperatorProfile(){return operatorUI.active||null;}
function selectedOperatorProfile(){const id=$('#operatorProfile')?.value||'';return operatorUI.profiles.find(p=>p.id===id)||null;}
function eventShortcut(e){const parts=[];if(e.ctrlKey)parts.push('CTRL');if(e.metaKey)parts.push('META');if(e.altKey)parts.push('ALT');if(e.shiftKey)parts.push('SHIFT');let key=String(e.key||'').toUpperCase();if(key===' ')key='SPACE';if(key==='CONTROL'||key==='META'||key==='ALT'||key==='SHIFT')return'';parts.push(key);return parts.join('+');}
function updateOperatorButtons(){const active=activeOperatorProfile(),selected=selectedOperatorProfile();for(const id of ['addOperatorHotkey','addOperatorMacro'])if($(`#${id}`))$(`#${id}`).disabled=!active;const del=$('#deleteOperatorProfile');if(del)del.disabled=!selected||Boolean(selected.is_active);const activate=$('#activateOperatorProfile');if(activate)activate.disabled=!selected||Boolean(selected.is_active);}
function renderOperatorProfile(){const select=$('#operatorProfile');if(!select)return;const previouslySelected=select.value;select.innerHTML=`<option value=''>Default fixed controls</option>`+operatorUI.profiles.map(p=>`<option value='${esc(p.id)}' ${p.is_active?'selected':''}>${esc(p.name)}${p.is_active?' · active':''}</option>`).join('');if(previouslySelected&&operatorUI.profiles.some(p=>p.id===previouslySelected))select.value=previouslySelected;const profile=activeOperatorProfile();$('#operatorHotkeys').innerHTML=profile?Object.entries(profile.hotkeys||{}).map(([key,command])=>`<code>${esc(key)}</code><span>${esc(command.replaceAll('_',' '))}</span>`).join(''):'<span class=muted>No custom hotkeys active.</span>';$('#operatorMacros').innerHTML=profile?(profile.macros||[]).map((m,i)=>`<button data-operator-macro='${i}'>${esc(m.name)}</button>`).join(''):' ';$$('[data-operator-macro]').forEach(b=>b.onclick=()=>runOperatorMacro(Number(b.dataset.operatorMacro)));updateOperatorButtons();}
async function loadOperatorProfiles(){try{const d=await api(`/shared-sky/studio/api/projects/${encodeURIComponent(projectId)}/operator-profiles`);operatorUI.profiles=d.profiles||[];operatorUI.active=operatorUI.profiles.find(p=>p.is_active)||null;renderOperatorProfile();}catch(e){handle(e);}}
async function activateOperatorProfileUI(){const profile=selectedOperatorProfile();if(!profile){$('#notice').textContent='Select a saved operator profile to activate.';return;}if(profile.is_active){$('#notice').textContent=`Operator profile “${profile.name}” is already active.`;return;}operatorUI.busy=true;try{const d=await api(`/shared-sky/studio/api/projects/${encodeURIComponent(projectId)}/operator-profiles/${encodeURIComponent(profile.id)}/activate`,{method:'POST'});await loadOperatorProfiles();$('#notice').textContent=`Operator profile “${d.profile?.name||'profile'}” activated.`;}catch(e){handle(e);}finally{operatorUI.busy=false;}}
async function createOperatorProfileUI(){const name=prompt('Operator profile name','Studio Operator');if(!name)return;try{await api(`/shared-sky/studio/api/projects/${encodeURIComponent(projectId)}/operator-profiles`,{method:'POST',body:JSON.stringify({name,hotkeys:{},macros:[],activate:true})});await loadOperatorProfiles();$('#notice').textContent='Operator profile created and activated. Add hotkeys or macros below.';}catch(e){handle(e);}}
async function saveActiveOperatorProfile(fields){const profile=activeOperatorProfile();if(!profile)throw new Error('Activate an operator profile first.');const body={name:profile.name,hotkeys:{...(profile.hotkeys||{})},macros:[...(profile.macros||[])],expected_version:profile.version,...fields};const d=await api(`/shared-sky/studio/api/projects/${encodeURIComponent(projectId)}/operator-profiles/${encodeURIComponent(profile.id)}`,{method:'PUT',body:JSON.stringify(body)});await loadOperatorProfiles();return d.profile;}
async function addOperatorHotkeyUI(){const profile=activeOperatorProfile();if(!profile)return;const shortcut=prompt('Hotkey (example CTRL+SHIFT+K)','');if(!shortcut)return;const command=(prompt(`Command: ${OPERATOR_COMMANDS.join(', ')}`,'undo')||'').trim();if(!OPERATOR_COMMANDS.includes(command)){handle(new Error('Unsupported operator command.'));return;}const hotkeys={...(profile.hotkeys||{})};hotkeys[shortcut]=command;try{await saveActiveOperatorProfile({hotkeys});$('#notice').textContent=`Hotkey added for ${command.replaceAll('_',' ')}.`;}catch(e){handle(e);}}
async function addOperatorMacroUI(){const profile=activeOperatorProfile();if(!profile)return;const name=prompt('Macro name','Studio Macro');if(!name)return;const raw=prompt(`Commands in order, comma-separated: ${OPERATOR_COMMANDS.join(', ')}`,'undo,redo');if(raw===null)return;const commands=raw.split(',').map(x=>x.trim()).filter(Boolean);if(!commands.length||commands.some(c=>!OPERATOR_COMMANDS.includes(c))){handle(new Error('Macro contains an unsupported operator command.'));return;}const programme=commands.some(c=>c==='cut'||c==='transition');if(programme&&!confirm('This macro changes Programme. It will require confirmation every time it runs. Create it?'))return;const macros=[...(profile.macros||[]),{name,commands,confirm_programme:programme}];try{await saveActiveOperatorProfile({macros});$('#notice').textContent=`Macro “${name}” added.`;}catch(e){handle(e);}}
async function deleteOperatorProfileUI(){const profile=selectedOperatorProfile();if(!profile)return;if(profile.is_active){$('#notice').textContent='Activate another profile before deleting this active profile.';return;}if(!confirm(`Delete inactive operator profile “${profile.name}”?`))return;try{await api(`/shared-sky/studio/api/projects/${encodeURIComponent(projectId)}/operator-profiles/${encodeURIComponent(profile.id)}`,{method:'DELETE'});await loadOperatorProfiles();$('#notice').textContent=`Operator profile “${profile.name}” deleted.`;}catch(e){handle(e);}}
async function operatorScene(direction){const scenes=state.project?.scenes||[],current=state.session?.preview_scene_id,index=scenes.findIndex(s=>s.id===current);if(index<0||!scenes.length)return;const target=Math.max(0,Math.min(scenes.length-1,index+direction));if(target===index)return;assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/preview`,{method:'POST',body:JSON.stringify({scene_id:scenes[target].id,expected_version:state.session.version})}));state.selected.clear();await loadHistory();}
async function operatorTransition(){assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_key:$('#transition').value,duration_ms:Number($('#duration').value),reduced_motion:matchMedia('(prefers-reduced-motion: reduce)').matches})}));const token=state.session.transition.transition_id,ms=state.session.transition.duration_ms||0;await new Promise(resolve=>setTimeout(resolve,ms));assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition/complete`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_id:token})}));}
async function operatorMarker(){if(!state.session?.broadcast_id)throw new Error('Attach a broadcast before adding a marker.');const seconds=Number($('#markerSeconds')?.value||0),label=$('#markerLabel')?.value||'Operator marker';const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/markers`,{method:'POST',body:JSON.stringify({offset_ms:Math.max(0,Math.round(seconds*1000)),label,marker_type:'highlight'})});if(typeof transportUI!=='undefined'&&d.marker){transportUI.markers.push(d.marker);renderMarkers();}}
async function executeOperatorCommand(command){if(operatorUI.busy)return;operatorUI.busy=true;try{if(command==='cut'){assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/cut`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}));}else if(command==='transition'){await operatorTransition();}else if(command==='undo'||command==='redo'){assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/${command}`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}));state.selected.clear();}else if(command==='scene_next'){await operatorScene(1);}else if(command==='scene_previous'){await operatorScene(-1);}else if(command==='marker_highlight'){await operatorMarker();}else{throw new Error('Unsupported operator command');}$('#notice').textContent=`Operator command completed: ${command.replaceAll('_',' ')}.`;}catch(e){await handle(e);throw e;}finally{operatorUI.busy=false;}}
async function runOperatorMacro(index){const profile=activeOperatorProfile(),macro=profile?.macros?.[index];if(!macro||operatorUI.busy)return;const programme=macro.commands.some(c=>c==='cut'||c==='transition');if(programme&&!confirm(`Run macro “${macro.name}”? It will change Programme.`))return;for(const command of macro.commands){try{await executeOperatorCommand(command);}catch{return;}}$('#notice').textContent=`Macro completed: ${macro.name}.`;}
document.addEventListener('keydown',e=>{if(!keySafe(e)||operatorUI.busy)return;const profile=activeOperatorProfile();if(!profile)return;const command=profile.hotkeys?.[eventShortcut(e)];if(!command)return;e.preventDefault();e.stopImmediatePropagation();if((command==='cut'||command==='transition')&&!confirm(`Run ${command.toUpperCase()} from custom operator hotkey?`))return;executeOperatorCommand(command).catch(()=>{});},{capture:true});
$('#operatorProfile').onchange=updateOperatorButtons;$('#activateOperatorProfile').onclick=activateOperatorProfileUI;$('#createOperatorProfile').onclick=createOperatorProfileUI;$('#addOperatorHotkey').onclick=addOperatorHotkeyUI;$('#addOperatorMacro').onclick=addOperatorMacroUI;$('#deleteOperatorProfile').onclick=deleteOperatorProfileUI;
loadOperatorProfiles();
"""


def enhanced_operator_html(project_id: str, base_html) -> str:
    html = base_html(project_id)
    if "id='operatorConsole'" in html:
        return html
    html = html.replace("</style>", OPERATOR_CSS + "</style>", 1)
    html = html.replace(
        "<h2>Inspector</h2>",
        OPERATOR_HTML + "<h2>Inspector</h2>",
        1,
    )
    html = html.replace("</script></body>", OPERATOR_JS + "</script></body>", 1)
    return html


def install_professional_operator_ui(app: Any) -> None:
    from .shared_sky_motion_graphics import install_shared_sky_motion_graphics
    from .shared_sky_professional_ingest_ui import install_professional_ingest_ui
    from .shared_sky_professional_motion_graphics_ui import install_professional_motion_graphics_ui

    install_shared_sky_motion_graphics(app)
    install_professional_ingest_ui(app)
    current = canvas.professional_html
    if not getattr(current, "_shared_sky_operator_ui", False):
        def wrapped(project_id: str) -> str:
            return enhanced_operator_html(project_id, current)

        for marker in ("_shared_sky_transport_toolbar", "_shared_sky_ingest_ui"):
            if getattr(current, marker, False):
                setattr(wrapped, marker, True)
        setattr(wrapped, "_shared_sky_operator_ui", True)
        canvas.professional_html = wrapped
    install_professional_motion_graphics_ui(app)


__all__ = [
    "OPERATOR_CSS",
    "OPERATOR_HTML",
    "OPERATOR_JS",
    "enhanced_operator_html",
    "install_professional_operator_ui",
]
