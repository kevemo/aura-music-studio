from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Professional Editor Lifecycle Inspector"])

PRO_EDITOR_LIFECYCLE_JS = r"""
(()=>{
  const byId=id=>document.getElementById(id);
  const root=byId('proInspectorControls');
  if(!root||byId('proLifecycleStacks'))return;
  const isPro=document.body.dataset.pro==='true';
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const style=document.createElement('style');
  style.textContent=`.prostack{border:1px solid var(--line);border-radius:10px;padding:8px;margin:8px 0;background:#ffffff04}.prostack h4{margin:0 0 6px;font-size:.7rem;color:var(--gold)}.prostackrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;padding:5px 0;border-bottom:1px solid #ffffff0a}.prostackrow:last-child{border-bottom:0}.prostackname{min-width:0;font-size:.66rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.prostackactions{display:flex;gap:4px;flex-wrap:wrap}.prostackactions .btn{padding:4px 6px;font-size:.6rem}.prostackmeta{display:block;color:var(--muted);font-size:.58rem;margin-top:2px}.prostackempty{font-size:.63rem;color:var(--muted)}.proediting{color:var(--cyan);font-size:.61rem;margin-top:5px}.problendrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:end}`;
  document.head.append(style);
  const lifecycle=document.createElement('div');
  lifecycle.id='proLifecycleStacks';
  lifecycle.innerHTML=`
    <div class="prostack"><h4>Blend mode</h4><div class="problendrow"><label class="label">Layer blend<select class="field" id="proBlendMode" ${isPro?'':'disabled'}><option value="normal">Normal</option><option value="multiply">Multiply</option><option value="screen">Screen</option><option value="overlay">Overlay</option><option value="soft_light">Soft Light</option><option value="hard_light">Hard Light</option><option value="darken">Darken</option><option value="lighten">Lighten</option><option value="difference">Difference</option></select></label><button class="btn" id="proApplyBlend" ${isPro?'':'disabled'}>Apply</button></div><div id="proBlendMeta" class="prostackmeta">Select an item to inspect its blend mode.</div></div>
    <div class="prostack"><h4>Current masks</h4><div id="proLifecycleMasks"></div><div id="proMaskEditing" class="proediting"></div></div>
    <div class="prostack"><h4>Current effects</h4><div id="proLifecycleEffects"></div><div id="proEffectEditing" class="proediting"></div></div>
    <div class="prostack"><h4>Current keyframe lanes</h4><div id="proLifecycleKeys"></div></div>`;
  root.append(lifecycle);

  let editingMask=null;
  let editingEffect=null;
  function current(){try{return selected||null}catch(_){return null}}
  function parseJSON(id,fallback){const raw=byId(id)?.value?.trim?.()||'';if(!raw)return fallback;return JSON.parse(raw)}
  function resetMaskEdit(){editingMask=null;const button=byId('proAddMask');if(button)button.textContent='Add mask';const note=byId('proMaskEditing');if(note)note.textContent=''}
  function resetEffectEdit(){editingEffect=null;const button=byId('proAddEffect');if(button)button.textContent='Add effect';const note=byId('proEffectEditing');if(note)note.textContent=''}
  function refreshGraph(response,itemId){
    try{
      if(response?.editor)state=response.editor;
      if(itemId)selected=branch().items.find(row=>row.id===itemId)||selected;
      render(false);fillInspector();renderPreview();
    }catch(_){}
    sync();
  }
  function actionButton(label,action,id,extra=''){
    return `<button class="btn" data-life-action="${action}" data-life-id="${esc(id)}" ${extra} ${isPro?'':'disabled'}>${label}</button>`;
  }
  function sync(){
    const item=current();
    const blend=byId('proBlendMode'),blendMeta=byId('proBlendMeta');
    if(!item){
      if(blend)blend.value='normal';
      if(blendMeta)blendMeta.textContent='Select an item to inspect its blend mode.';
      byId('proLifecycleMasks').innerHTML='<div class="prostackempty">Select an item to inspect its masks.</div>';
      byId('proLifecycleEffects').innerHTML='<div class="prostackempty">Select an item to inspect its effects.</div>';
      byId('proLifecycleKeys').innerHTML='<div class="prostackempty">Select an item to inspect its keyframes.</div>';
      resetMaskEdit();resetEffectEdit();return;
    }
    if(blend)blend.value=item.blend_mode||'normal';
    if(blendMeta)blendMeta.textContent=`Current: ${String(item.blend_mode||'normal').replaceAll('_',' ')} · stored in the reversible edit graph.`;
    if(editingMask&&editingMask.itemId!==item.id)resetMaskEdit();
    if(editingEffect&&editingEffect.itemId!==item.id)resetEffectEdit();
    const masks=item.masks||[];
    byId('proLifecycleMasks').innerHTML=masks.length?masks.map((mask,index)=>`<div class="prostackrow"><div class="prostackname">${esc(mask.name||mask.shape||'Mask')}<span class="prostackmeta">${esc(mask.shape)} · ${esc(mask.mode)} · feather ${Number(mask.feather||0).toFixed(1)}</span></div><div class="prostackactions">${actionButton('Edit','mask-edit',mask.id)}${actionButton('Delete','mask-delete',mask.id)}</div></div>`).join(''):'<div class="prostackempty">No masks on this item.</div>';
    const effects=item.effects||[];
    byId('proLifecycleEffects').innerHTML=effects.length?effects.map((effect,index)=>`<div class="prostackrow"><div class="prostackname">${esc(effect.type||'Effect')}<span class="prostackmeta">mix ${Number(effect.mix??1).toFixed(2)} · position ${index+1}/${effects.length}</span></div><div class="prostackactions">${actionButton('Edit','effect-edit',effect.id)}${actionButton('↑','effect-up',effect.id,`data-life-index="${index}"`)}${actionButton('↓','effect-down',effect.id,`data-life-index="${index}"`)}${actionButton('Delete','effect-delete',effect.id)}</div></div>`).join(''):'<div class="prostackempty">No effects on this item.</div>';
    const lanes=item.keyframes||{};
    const names=Object.keys(lanes);
    byId('proLifecycleKeys').innerHTML=names.length?names.map(name=>`<div class="prostackrow"><div class="prostackname">${esc(name)}<span class="prostackmeta">${(lanes[name]||[]).length} point${(lanes[name]||[]).length===1?'':'s'}</span></div><div class="prostackactions">${actionButton('Delete lane','key-delete',name)}</div></div>`).join(''):'<div class="prostackempty">No keyframe lanes on this item.</div>';
  }

  const blendApply=byId('proApplyBlend');
  if(blendApply)blendApply.onclick=async()=>{
    const item=current();if(!item)return status('Select an item first.',true);
    if(!isPro)return status('Professional blend modes require Pro.',true);
    try{
      const blend_mode=byId('proBlendMode').value;
      const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}`),{method:'PATCH',body:JSON.stringify({changes:{blend_mode}})});
      refreshGraph(response,item.id);status(`Blend mode saved · ${blend_mode.replaceAll('_',' ')}. Undo can restore it.`);
    }catch(error){status(error.message,true)}
  };

  const maskAdd=byId('proAddMask');
  const baseMaskAdd=maskAdd?.onclick;
  if(maskAdd)maskAdd.onclick=async()=>{
    const item=current();
    if(!editingMask||!item||editingMask.itemId!==item.id)return baseMaskAdd?.();
    try{
      const points=parseJSON('proMaskPoints',[]);if(!Array.isArray(points))throw new Error('Mask points must be a JSON array.');
      const changes={shape:byId('proMaskShape').value,mode:byId('proMaskMode').value,feather:Number(byId('proMaskFeather').value||0),expansion:Number(byId('proMaskExpansion').value||0),inverted:byId('proMaskInvert').checked,points};
      const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}/masks/${encodeURIComponent(editingMask.maskId)}`),{method:'PATCH',body:JSON.stringify({changes})});
      resetMaskEdit();refreshGraph(response,item.id);status('Mask changes saved to the non-destructive edit graph.');
    }catch(error){status(error.message,true)}
  };

  const effectAdd=byId('proAddEffect');
  const baseEffectAdd=effectAdd?.onclick;
  if(effectAdd)effectAdd.onclick=async()=>{
    const item=current();
    if(!editingEffect||!item||editingEffect.itemId!==item.id)return baseEffectAdd?.();
    try{
      const parameters=parseJSON('proEffectParams',{});if(!parameters||Array.isArray(parameters)||typeof parameters!=='object')throw new Error('Effect parameters must be a JSON object.');
      const changes={type:byId('proEffectType').value,mix:Number(byId('proEffectMix').value||1),parameters};
      const response=await api(endpoint(`/item/${encodeURIComponent(item.id)}/effects/${encodeURIComponent(editingEffect.effectId)}`),{method:'PATCH',body:JSON.stringify({changes})});
      resetEffectEdit();refreshGraph(response,item.id);status('Effect changes saved.');
    }catch(error){status(error.message,true)}
  };

  lifecycle.addEventListener('click',async event=>{
    const button=event.target?.closest?.('button[data-life-action]');if(!button)return;
    const item=current();if(!item)return status('Select an item first.',true);
    const action=button.dataset.lifeAction,id=button.dataset.lifeId;
    try{
      if(action==='mask-edit'){
        const mask=(item.masks||[]).find(row=>row.id===id);if(!mask)return status('Mask no longer exists.',true);
        editingMask={itemId:item.id,maskId:id};byId('proMaskShape').value=mask.shape;byId('proMaskMode').value=mask.mode;byId('proMaskFeather').value=Number(mask.feather||0);byId('proMaskExpansion').value=Number(mask.expansion||0);byId('proMaskInvert').checked=Boolean(mask.inverted);byId('proMaskPoints').value=JSON.stringify(mask.points||[]);maskAdd.textContent='Save mask changes';byId('proMaskEditing').textContent=`Editing ${mask.name||mask.shape}. Save changes with the mask button above.`;return;
      }
      if(action==='effect-edit'){
        const effect=(item.effects||[]).find(row=>row.id===id);if(!effect)return status('Effect no longer exists.',true);
        editingEffect={itemId:item.id,effectId:id};byId('proEffectType').value=effect.type;byId('proEffectMix').value=Number(effect.mix??1);byId('proEffectParams').value=JSON.stringify(effect.parameters||{},null,2);effectAdd.textContent='Save effect changes';byId('proEffectEditing').textContent=`Editing ${effect.type}. Save changes with the effect button above.`;return;
      }
      if(action==='mask-delete'){
        const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}/masks/${encodeURIComponent(id)}`),{method:'DELETE'});if(editingMask?.maskId===id)resetMaskEdit();refreshGraph(response,item.id);status('Mask deleted. Undo can restore it.');return;
      }
      if(action==='effect-delete'){
        const response=await api(endpoint(`/item/${encodeURIComponent(item.id)}/effects/${encodeURIComponent(id)}`),{method:'DELETE'});if(editingEffect?.effectId===id)resetEffectEdit();refreshGraph(response,item.id);status('Effect deleted. Undo can restore it.');return;
      }
      if(action==='effect-up'||action==='effect-down'){
        const currentIndex=Number(button.dataset.lifeIndex||0),max=Math.max(0,(item.effects||[]).length-1),index=Math.max(0,Math.min(max,currentIndex+(action==='effect-up'?-1:1)));
        const response=await api(endpoint(`/item/${encodeURIComponent(item.id)}/effects/${encodeURIComponent(id)}/reorder`),{method:'POST',body:JSON.stringify({index})});refreshGraph(response,item.id);status(`Effect moved to position ${index+1}.`);return;
      }
      if(action==='key-delete'){
        const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}/keyframes/${encodeURIComponent(id)}`),{method:'DELETE'});refreshGraph(response,item.id);status(`Keyframe lane deleted · ${id}. Undo can restore it.`);return;
      }
    }catch(error){status(error.message,true)}
  });

  document.addEventListener('click',event=>{if(event.target?.closest?.('.clip,.asset'))setTimeout(sync,0)});
  sync();
})();
"""


@router.get("/creative/editor-pro-lifecycle-controls.js", include_in_schema=False)
def editor_pro_lifecycle_controls_js():
    return Response(PRO_EDITOR_LIFECYCLE_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


__all__ = ["PRO_EDITOR_LIFECYCLE_JS", "editor_pro_lifecycle_controls_js", "router"]
