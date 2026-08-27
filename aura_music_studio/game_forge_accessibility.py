from __future__ import annotations


_ACCESSIBILITY_STYLE = r"""
<style id='aura-runtime-accessibility'>
:focus-visible{outline:3px solid #5be1ff!important;outline-offset:3px!important}
button,a,[role='button']{min-height:44px;min-width:44px}
#aura-mobile-controls{position:fixed;right:max(12px,env(safe-area-inset-right));bottom:max(12px,env(safe-area-inset-bottom));z-index:7;display:grid;grid-template-columns:repeat(3,48px);grid-template-rows:repeat(2,48px);gap:6px;touch-action:none}
#aura-mobile-controls button{border:1px solid #ffffff55;border-radius:12px;background:#07101fe8;color:#fff;font:900 20px/1 system-ui,sans-serif;box-shadow:0 5px 18px #0007;touch-action:none;user-select:none;-webkit-user-select:none}
#aura-mobile-controls .up{grid-column:2}.aura-left{grid-column:1}.aura-down{grid-column:2}.aura-right{grid-column:3}
#aura-sr-status{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
@media (pointer:fine) and (min-width:900px){#aura-mobile-controls{display:none}}
@media (max-width:600px){#hud{left:8px!important;top:max(8px,env(safe-area-inset-top))!important;max-width:calc(100vw - 16px)!important;font-size:13px}#status{right:8px!important;bottom:calc(124px + env(safe-area-inset-bottom))!important;max-width:70vw}#media-controls{left:8px!important;bottom:max(8px,env(safe-area-inset-bottom))!important;max-width:calc(100vw - 190px)}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}}
</style>
"""

_ACCESSIBILITY_BODY = r"""
<div id='aura-mobile-controls' aria-label='Game movement controls'>
  <button class='up' type='button' data-aura-key='ArrowUp' aria-label='Move forward'>▲</button>
  <button class='aura-left' type='button' data-aura-key='ArrowLeft' aria-label='Move left'>◀</button>
  <button class='aura-down' type='button' data-aura-key='ArrowDown' aria-label='Move backward'>▼</button>
  <button class='aura-right' type='button' data-aura-key='ArrowRight' aria-label='Move right'>▶</button>
</div>
<div id='aura-sr-status' role='status' aria-live='polite'>Aura game runtime ready.</div>
<script id='aura-runtime-accessibility-js'>
(()=>{
  const canvas=document.getElementById('game');
  if(canvas){canvas.tabIndex=0;canvas.setAttribute('role','application');canvas.setAttribute('aria-label','Interactive Aura 3D game canvas');}
  const status=document.getElementById('status');
  if(status){status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.setAttribute('aria-atomic','true');}
  const fallback=document.getElementById('fallback');if(fallback){fallback.setAttribute('role','alert');}
  const cutscene=document.getElementById('cutscene');if(cutscene){cutscene.setAttribute('aria-label','Game cutscene video');}
  const audio=document.getElementById('audio-toggle');if(audio){audio.setAttribute('aria-label','Toggle game audio');}
  const video=document.getElementById('video-toggle');if(video){video.setAttribute('aria-label','Play game cutscene');}
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const setReduced=()=>{document.documentElement.dataset.auraReducedMotion=reduced.matches?'true':'false';};setReduced();
  if(reduced.addEventListener)reduced.addEventListener('change',setReduced);
  const dispatch=(type,key)=>window.dispatchEvent(new KeyboardEvent(type,{key,bubbles:true}));
  document.querySelectorAll('[data-aura-key]').forEach(button=>{
    const key=button.dataset.auraKey;
    const down=e=>{e.preventDefault();dispatch('keydown',key);};
    const up=e=>{e.preventDefault();dispatch('keyup',key);};
    button.addEventListener('pointerdown',down);button.addEventListener('pointerup',up);button.addEventListener('pointercancel',up);button.addEventListener('pointerleave',up);
  });
})();
</script>
"""


def harden_game_runtime_html(html: str) -> str:
    """Add same-document mobile/accessibility controls without weakening the runtime CSP."""
    if "id='aura-runtime-accessibility'" in html:
        return html
    if "</head>" not in html or "</body>" not in html:
        raise ValueError("Game runtime HTML is missing document anchors")
    html = html.replace("</head>", _ACCESSIBILITY_STYLE + "</head>", 1)
    return html.replace("</body>", _ACCESSIBILITY_BODY + "</body>", 1)


__all__ = ["harden_game_runtime_html"]
