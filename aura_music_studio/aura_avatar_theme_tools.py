from __future__ import annotations

from . import aura_avatar_bootstrap

_MARKER = "/* AURA_NICHE_THEME_RUNTIME */"

_THEME_JS = r'''
/* AURA_NICHE_THEME_RUNTIME */
const AURA_THEMES={
  default:{heart:0xff43c8,circuit:0xb85cff,glow:'rgba(201,70,255,.34)',label:'Aura'},
  music:{heart:0xff3fcf,circuit:0x9e58ff,glow:'rgba(208,58,255,.40)',label:'Music'},
  singing:{heart:0xff3fcf,circuit:0x9e58ff,glow:'rgba(208,58,255,.40)',label:'Music'},
  gaming:{heart:0x43e7ff,circuit:0x865cff,glow:'rgba(61,211,255,.36)',label:'Gaming'},
  beauty:{heart:0xff72b9,circuit:0xf0b76c,glow:'rgba(255,114,185,.32)',label:'Beauty'},
  fashion:{heart:0xff72b9,circuit:0xd690ff,glow:'rgba(255,114,185,.32)',label:'Fashion'},
  food:{heart:0xffa855,circuit:0xff5d92,glow:'rgba(255,151,81,.32)',label:'Food'},
  cooking:{heart:0xffa855,circuit:0xff5d92,glow:'rgba(255,151,81,.32)',label:'Food'},
  fitness:{heart:0xff6a42,circuit:0xff35ab,glow:'rgba(255,82,91,.32)',label:'Fitness'},
  art:{heart:0xc463ff,circuit:0x43dcff,glow:'rgba(188,87,255,.36)',label:'Art'},
  business:{heart:0xf0c86c,circuit:0x8d78ff,glow:'rgba(240,200,108,.30)',label:'Business'},
  entrepreneurship:{heart:0xf0c86c,circuit:0x8d78ff,glow:'rgba(240,200,108,.30)',label:'Business'},
  spiritual:{heart:0xe75fff,circuit:0xff60aa,glow:'rgba(220,79,255,.38)',label:'Spiritual'},
  wellbeing:{heart:0xe75fff,circuit:0xff60aa,glow:'rgba(220,79,255,.38)',label:'Wellbeing'},
  education:{heart:0x64baff,circuit:0xb16dff,glow:'rgba(100,186,255,.32)',label:'Education'},
  technology:{heart:0x50e8ff,circuit:0xb84fff,glow:'rgba(80,232,255,.34)',label:'Technology'},
  lifestyle:{heart:0xf08fc8,circuit:0x79d6c5,glow:'rgba(240,143,200,.30)',label:'Lifestyle'},
  asmr:{heart:0xc68cff,circuit:0x75d8e8,glow:'rgba(198,140,255,.30)',label:'ASMR'},
  dance:{heart:0xff4fae,circuit:0x6c8cff,glow:'rgba(255,79,174,.34)',label:'Dance'}
};

AuraEmbodiedRuntime.prototype.setTheme=function(name,{persist=true}={}){
  const key=(name||'default').toString().trim().toLowerCase().replace(/[^a-z0-9]+/g,'');
  const theme=AURA_THEMES[key]||AURA_THEMES.default;
  this.themeName=AURA_THEMES[key]?key:'default';
  this.theme=theme;
  for(const material of this.heartMaterials||[]){
    if(material.emissive?.setHex)material.emissive.setHex(theme.heart);
    if(material.color?.setHex&&((material.name||'').toLowerCase().includes('heart')))material.color.setHex(theme.heart);
  }
  for(const material of this.circuitMaterials||[]){
    if(material.emissive?.setHex)material.emissive.setHex(theme.circuit);
  }
  if(this.root)this.root.style.filter='drop-shadow(0 18px 35px rgba(0,0,0,.42)) drop-shadow(0 0 24px '+theme.glow+')';
  document.documentElement.dataset.auraTheme=this.themeName;
  if(persist){try{sessionStorage.setItem('aura-energy-theme',this.themeName);}catch(_){}}
  dispatchEvent(new CustomEvent('aura:theme-changed',{detail:{theme:this.themeName,label:theme.label}}));
  return this.themeName;
};

AuraEmbodiedRuntime.prototype.restoreTheme=function(){
  let requested='';
  try{requested=sessionStorage.getItem('aura-energy-theme')||'';}catch(_){}
  requested=document.documentElement.dataset.niche||document.body?.dataset?.niche||requested||'default';
  return this.setTheme(requested,{persist:false});
};

const _auraOriginalDiscover=AuraEmbodiedRuntime.prototype.discoverEmissionMaterials;
AuraEmbodiedRuntime.prototype.discoverEmissionMaterials=function(){
  _auraOriginalDiscover.call(this);
  this.restoreTheme();
};

const _auraOriginalHuman=AuraEmbodiedRuntime.prototype.updateHumanMotion;
AuraEmbodiedRuntime.prototype.updateHumanMotion=function(t,dt){
  _auraOriginalHuman.call(this,t,dt);
  if(!this.vrm)return;
  // Subtle facial life: relaxed at rest, attentive while listening, brighter on celebration.
  const idleWarmth=this.state==='idle'?(0.04+0.018*Math.sin(t*.0009)):0;
  const listening=this.state==='listening'?0.08:0;
  const celebration=this.state==='celebrate'?0.25:0;
  this.expr('relaxed',idleWarmth+listening);
  this.expr('happy',celebration);
};

addEventListener('aura:theme',e=>{
  const name=e.detail?.theme||e.detail?.niche||e.detail?.name||'default';
  if(window.AuraAvatar?.setTheme)window.AuraAvatar.setTheme(name);
});
addEventListener('aura:avatar-ready',()=>{
  if(window.AuraAvatar?.restoreTheme)window.AuraAvatar.restoreTheme();
});
'''


if _MARKER not in aura_avatar_bootstrap.PATCHED_RUNTIME_JS:
    # Insert before the module's export statement so AuraEmbodiedRuntime is still in lexical scope.
    export_line = "export {AuraEmbodiedRuntime,aura};"
    if export_line in aura_avatar_bootstrap.PATCHED_RUNTIME_JS:
        aura_avatar_bootstrap.PATCHED_RUNTIME_JS = aura_avatar_bootstrap.PATCHED_RUNTIME_JS.replace(
            export_line,
            _THEME_JS + "\n" + export_line,
            1,
        )
    else:
        aura_avatar_bootstrap.PATCHED_RUNTIME_JS += "\n" + _THEME_JS
