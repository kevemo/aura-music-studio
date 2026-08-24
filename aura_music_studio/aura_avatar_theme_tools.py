from __future__ import annotations

from . import aura_avatar_bootstrap

_MARKER = "/* AURA_NICHE_THEME_RUNTIME */"

_THEME_JS = r'''
/* AURA_NICHE_THEME_RUNTIME */
const AURA_THEMES={
  default:{heart:0xff43c8,circuit:0xb85cff,eyes:0xff70e6,glow:[201,70,255],label:'Aura'},
  music:{heart:0xff3fcf,circuit:0x9e58ff,eyes:0xff79ef,glow:[208,58,255],label:'Music'},
  singing:{heart:0xff3fcf,circuit:0x9e58ff,eyes:0xff79ef,glow:[208,58,255],label:'Music'},
  gaming:{heart:0x43e7ff,circuit:0x865cff,eyes:0x7af2ff,glow:[61,211,255],label:'Gaming'},
  beauty:{heart:0xff72b9,circuit:0xf0b76c,eyes:0xffa4d2,glow:[255,114,185],label:'Beauty'},
  fashion:{heart:0xff72b9,circuit:0xd690ff,eyes:0xffa4e8,glow:[255,114,185],label:'Fashion'},
  food:{heart:0xffa855,circuit:0xff5d92,eyes:0xffc184,glow:[255,151,81],label:'Food'},
  cooking:{heart:0xffa855,circuit:0xff5d92,eyes:0xffc184,glow:[255,151,81],label:'Food'},
  fitness:{heart:0xff6a42,circuit:0xff35ab,eyes:0xff8d75,glow:[255,82,91],label:'Fitness'},
  art:{heart:0xc463ff,circuit:0x43dcff,eyes:0xde9bff,glow:[188,87,255],label:'Art'},
  business:{heart:0xf0c86c,circuit:0xcbbcff,eyes:0xffe4a3,glow:[240,200,108],label:'Business'},
  entrepreneurship:{heart:0xf0c86c,circuit:0xcbbcff,eyes:0xffe4a3,glow:[240,200,108],label:'Business'},
  spiritual:{heart:0xe75fff,circuit:0xff60aa,eyes:0xf1a0ff,glow:[220,79,255],label:'Spiritual'},
  wellbeing:{heart:0xe75fff,circuit:0xff60aa,eyes:0xf1a0ff,glow:[220,79,255],label:'Wellbeing'},
  education:{heart:0x64baff,circuit:0xb16dff,eyes:0x9ed8ff,glow:[100,186,255],label:'Education'},
  technology:{heart:0x50e8ff,circuit:0xb84fff,eyes:0x8ef5ff,glow:[80,232,255],label:'Technology'},
  lifestyle:{heart:0xf08fc8,circuit:0x79d6c5,eyes:0xffb8e2,glow:[240,143,200],label:'Lifestyle'},
  asmr:{heart:0xc68cff,circuit:0x75d8e8,eyes:0xe0bdff,glow:[198,140,255],label:'ASMR'},
  dance:{heart:0xff4fae,circuit:0x6c8cff,eyes:0xff8ccc,glow:[255,79,174],label:'Dance'}
};

// Communication state modulates the active page palette rather than replacing Aura's identity.
// hue = subtle HSL displacement; pulse/rate/boost control the visual energy signature.
const AURA_ENERGY_MODES={
  idle:{pulse:.10,rate:1.0,boost:0,hue:0,light:0},
  listening:{pulse:.14,rate:1.45,boost:.25,hue:.018,light:.025},
  speaking:{pulse:.30,rate:3.15,boost:.55,hue:-.018,light:.045},
  thinking:{pulse:.18,rate:.72,boost:.38,hue:.045,light:.015},
  creating:{pulse:.34,rate:2.05,boost:.72,hue:.07,light:.05},
  translating:{pulse:.22,rate:1.7,boost:.46,hue:-.065,light:.035},
  guiding:{pulse:.18,rate:1.35,boost:.30,hue:.025,light:.025},
  celebrate:{pulse:.44,rate:3.8,boost:.95,hue:-.04,light:.08}
};

const AURA_PAGE_THEME_HINTS=[
  [/\/(recording-studio|production-suite|daw|take-manager|studio)(\/|$)/,'music'],
  [/\/(visual-studio|visual|image|video)(\/|$)/,'art'],
  [/\/(owner|admin)(\/|$)/,'business'],
  [/\/esp(\/|$)/,'business'],
  [/\/aura(\/|$)/,'spiritual']
];

const auraThemeKey=value=>(value||'').toString().trim().toLowerCase().replace(/[^a-z0-9]+/g,'');
const auraThemeColor=value=>new THREE.Color(value);

AuraEmbodiedRuntime.prototype.detectPageEnergyTheme=function(){
  const html=document.documentElement,body=document.body;
  const explicit=
    html?.dataset?.niche||body?.dataset?.niche||
    html?.dataset?.workspace||body?.dataset?.workspace||
    html?.dataset?.section||body?.dataset?.section||
    document.querySelector('meta[name="aura-theme"]')?.content||'';
  const key=auraThemeKey(explicit);
  if(AURA_THEMES[key])return key;
  const path=(location.pathname||'/').toLowerCase();
  for(const [pattern,theme] of AURA_PAGE_THEME_HINTS)if(pattern.test(path))return theme;
  try{
    const saved=auraThemeKey(sessionStorage.getItem('aura-energy-theme')||'');
    if(AURA_THEMES[saved])return saved;
  }catch(_){}
  return 'default';
};

AuraEmbodiedRuntime.prototype.setTheme=function(name,{persist=true,immediate=false}={}){
  const key=auraThemeKey(name);
  const theme=AURA_THEMES[key]||AURA_THEMES.default;
  this.themeName=AURA_THEMES[key]?key:'default';
  this.theme=theme;
  this.energyTarget={
    heart:auraThemeColor(theme.heart),
    circuit:auraThemeColor(theme.circuit),
    eyes:auraThemeColor(theme.eyes)
  };
  if(immediate||!this.energyColors){
    this.energyColors={
      heart:this.energyTarget.heart.clone(),
      circuit:this.energyTarget.circuit.clone(),
      eyes:this.energyTarget.eyes.clone()
    };
  }
  document.documentElement.dataset.auraTheme=this.themeName;
  if(persist){try{sessionStorage.setItem('aura-energy-theme',this.themeName);}catch(_){}}
  dispatchEvent(new CustomEvent('aura:theme-changed',{detail:{theme:this.themeName,label:theme.label}}));
  return this.themeName;
};

AuraEmbodiedRuntime.prototype.restoreTheme=function(){
  return this.setTheme(this.detectPageEnergyTheme(),{persist:false,immediate:!this.energyColors});
};

AuraEmbodiedRuntime.prototype.setEnergyMode=function(mode,{duration=0}={}){
  const key=AURA_ENERGY_MODES[mode]?mode:'idle';
  this.energyMode=key;
  clearTimeout(this._energyModeTimer);
  if(duration>0)this._energyModeTimer=setTimeout(()=>this.setEnergyMode(this.state==='speaking'?'speaking':this.state==='listening'?'listening':this.state==='thinking'?'thinking':'idle'),duration);
  dispatchEvent(new CustomEvent('aura:energy-mode-changed',{detail:{mode:key,theme:this.themeName||'default'}}));
  return key;
};

const _auraOriginalSetState=AuraEmbodiedRuntime.prototype.setState;
AuraEmbodiedRuntime.prototype.setState=function(state){
  _auraOriginalSetState.call(this,state);
  const mapped={speaking:'speaking',listening:'listening',thinking:'thinking',walking:'guiding',pointing:'guiding',presenting:'guiding',celebrate:'celebrate'}[this.state];
  if(mapped)this.setEnergyMode(mapped,{duration:this.state==='celebrate'?1800:0});
  else if(!['creating','translating'].includes(this.energyMode))this.setEnergyMode('idle');
};

const _auraOriginalDiscover=AuraEmbodiedRuntime.prototype.discoverEmissionMaterials;
AuraEmbodiedRuntime.prototype.discoverEmissionMaterials=function(){
  _auraOriginalDiscover.call(this);
  this.eyeMaterials=[];
  if(this.vrm){
    this.vrm.scene.traverse(obj=>{
      if(!obj.isMesh)return;
      const mats=Array.isArray(obj.material)?obj.material:[obj.material];
      for(const material of mats){
        if(!material)continue;
        const name=(material.name||'').toLowerCase();
        if(name.includes('aura_eyes')||name==='eyes'||name.includes('eye_glow')||name.includes('iris')){
          if(!this.eyeMaterials.includes(material))this.eyeMaterials.push(material);
        }
      }
    });
  }
  this.restoreTheme();
  this.setEnergyMode(this.state==='speaking'?'speaking':this.state==='listening'?'listening':this.state==='thinking'?'thinking':'idle');
};

// The older runtime has material discovery inline in loadModel rather than as a method.
// This fallback guarantees eye discovery and theme restoration after the model is ready.
addEventListener('aura:avatar-ready',()=>{
  const aura=window.AuraAvatar;
  if(!aura)return;
  if(!Array.isArray(aura.eyeMaterials))aura.eyeMaterials=[];
  if(aura.vrm&&aura.eyeMaterials.length===0){
    aura.vrm.scene.traverse(obj=>{
      if(!obj.isMesh)return;
      for(const material of(Array.isArray(obj.material)?obj.material:[obj.material])){
        if(!material)continue;
        const name=(material.name||'').toLowerCase();
        if(name.includes('aura_eyes')||name==='eyes'||name.includes('eye_glow')||name.includes('iris')){
          if(!aura.eyeMaterials.includes(material))aura.eyeMaterials.push(material);
        }
      }
    });
  }
  aura.restoreTheme?.();
  aura.setEnergyMode?.('idle');
});

const _auraOriginalHuman=AuraEmbodiedRuntime.prototype.updateHumanMotion;
AuraEmbodiedRuntime.prototype.updateHumanMotion=function(t,dt){
  _auraOriginalHuman.call(this,t,dt);
  if(!this.vrm)return;
  const idleWarmth=this.state==='idle'?(0.04+0.018*Math.sin(t*.0009)):0;
  const listening=this.state==='listening'?0.08:0;
  const celebration=this.state==='celebrate'?0.25:0;
  this.expr('relaxed',idleWarmth+listening);
  this.expr('happy',celebration);
  this.expr('auraListening',this.energyMode==='listening'?.15:0);
  this.expr('auraFocused',this.energyMode==='thinking'?.13:0);
  this.expr('auraCelebrate',this.energyMode==='celebrate'?.30:0);
  this.expr('auraCurious',this.energyMode==='translating'?.10:0);
};

const _auraOriginalEmission=AuraEmbodiedRuntime.prototype.updateEmission;
AuraEmbodiedRuntime.prototype.updateEmission=function(t){
  // Keep the base implementation compatible, then apply Aura's richer live-energy layer.
  try{_auraOriginalEmission.call(this,t);}catch(_){}
  if(!this.theme)this.restoreTheme();
  if(!this.energyColors||!this.energyTarget)return;
  const mode=AURA_ENERGY_MODES[this.energyMode||'idle']||AURA_ENERGY_MODES.idle;
  const reduced=matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const motionScale=reduced?.28:1;
  const phase=t*.001*mode.rate;
  const wave=(.5+.5*Math.sin(phase*Math.PI*2))*mode.pulse*motionScale;
  const speech=this.energyMode==='speaking'?(this.speechEnergy||0):0;
  const energy=mode.boost+wave+speech*.95;

  const targetHeart=this.energyTarget.heart.clone().offsetHSL(mode.hue,0,mode.light+speech*.035);
  const targetCircuit=this.energyTarget.circuit.clone().offsetHSL(mode.hue*.72,0,mode.light*.70+speech*.02);
  const targetEyes=this.energyTarget.eyes.clone().offsetHSL(mode.hue*1.1,0,mode.light+speech*.025);
  const blend=reduced?.045:.085;
  this.energyColors.heart.lerp(targetHeart,blend);
  this.energyColors.circuit.lerp(targetCircuit,blend);
  this.energyColors.eyes.lerp(targetEyes,blend);

  for(const material of this.heartMaterials||[]){
    if(material.emissive?.copy)material.emissive.copy(this.energyColors.heart);
    if('emissiveIntensity'in material)material.emissiveIntensity=.82+energy*1.35;
    material.needsUpdate=true;
  }
  for(const material of this.circuitMaterials||[]){
    if(material.emissive?.copy)material.emissive.copy(this.energyColors.circuit);
    if('emissiveIntensity'in material)material.emissiveIntensity=.42+energy*.82;
    material.needsUpdate=true;
  }
  for(const material of this.eyeMaterials||[]){
    if(material.emissive?.copy)material.emissive.copy(this.energyColors.eyes);
    else if(material.color?.copy)material.color.copy(this.energyColors.eyes);
    if('emissiveIntensity'in material)material.emissiveIntensity=.72+energy*.92;
    material.needsUpdate=true;
  }

  // CSS glow is intentionally throttled to reduce mobile compositing cost.
  if(this.root&&(!this._energyLastCss||t-this._energyLastCss>90)){
    this._energyLastCss=t;
    const [r,g,b]=this.theme.glow;
    const alpha=Math.min(.56,.22+energy*.15);
    this.root.style.filter=`drop-shadow(0 18px 35px rgba(0,0,0,.42)) drop-shadow(0 0 ${18+Math.round(energy*15)}px rgba(${r},${g},${b},${alpha.toFixed(3)}))`;
  }
};

function auraApplyContextTheme(){
  const aura=window.AuraAvatar;
  if(aura?.restoreTheme)aura.restoreTheme();
}

addEventListener('aura:theme',e=>{
  const name=e.detail?.theme||e.detail?.niche||e.detail?.name||'default';
  if(window.AuraAvatar?.setTheme)window.AuraAvatar.setTheme(name);
});
addEventListener('aura:energy-mode',e=>window.AuraAvatar?.setEnergyMode?.(e.detail?.mode||'idle',{duration:Number(e.detail?.duration)||0}));
addEventListener('aura:creating',e=>window.AuraAvatar?.setEnergyMode?.(e.detail?.active===false?'idle':'creating'));
addEventListener('aura:translating',e=>window.AuraAvatar?.setEnergyMode?.(e.detail?.active===false?'idle':'translating'));
addEventListener('aura:researching',e=>window.AuraAvatar?.setEnergyMode?.(e.detail?.active===false?'idle':'thinking'));
addEventListener('aura:guide',()=>window.AuraAvatar?.setEnergyMode?.('guiding',{duration:1800}));
addEventListener('aura:celebrate',()=>window.AuraAvatar?.setEnergyMode?.('celebrate',{duration:1800}));
addEventListener('aura:page-context',auraApplyContextTheme);
addEventListener('popstate',auraApplyContextTheme);
addEventListener('hashchange',auraApplyContextTheme);

// Keep page-aware energy correct in SPA navigation without forcing a reload.
for(const method of ['pushState','replaceState']){
  const original=history[method];
  if(typeof original==='function'&&!original.__auraWrapped){
    const wrapped=function(...args){const value=original.apply(this,args);queueMicrotask(auraApplyContextTheme);return value;};
    wrapped.__auraWrapped=true;history[method]=wrapped;
  }
}

if(typeof MutationObserver!=='undefined'){
  const observer=new MutationObserver(mutations=>{
    if(mutations.some(m=>['data-niche','data-workspace','data-section'].includes(m.attributeName)))auraApplyContextTheme();
  });
  observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-niche','data-workspace','data-section']});
  if(document.body)observer.observe(document.body,{attributes:true,attributeFilter:['data-niche','data-workspace','data-section']});
}
'''


if _MARKER not in aura_avatar_bootstrap.PATCHED_RUNTIME_JS:
    # Insert before the module's export statement so AuraEmbodiedRuntime and THREE remain in lexical scope.
    export_line = "export {AuraEmbodiedRuntime,aura};"
    if export_line in aura_avatar_bootstrap.PATCHED_RUNTIME_JS:
        aura_avatar_bootstrap.PATCHED_RUNTIME_JS = aura_avatar_bootstrap.PATCHED_RUNTIME_JS.replace(
            export_line,
            _THEME_JS + "\n" + export_line,
            1,
        )
    else:
        aura_avatar_bootstrap.PATCHED_RUNTIME_JS += "\n" + _THEME_JS
