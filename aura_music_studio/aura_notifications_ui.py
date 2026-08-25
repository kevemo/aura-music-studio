from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import Response

router = APIRouter(include_in_schema=False)

NOTIFICATIONS_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  const $=id=>document.getElementById(id);
  function esc(v){try{return window.esc(v)}catch(_){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}}
  async function request(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function when(value){if(!value)return '';try{return new Date(value).toLocaleString()}catch(_){return String(value)}}

  function ensureButton(){
    const foot=document.querySelector('.sideFoot');if(!foot)return null;
    let button=$('auraNotificationsButton');if(button)return button;
    button=document.createElement('button');button.id='auraNotificationsButton';button.className='btn';
    const label=document.createElement('span');label.textContent='🔔 Notifications';button.append(label);
    const badge=document.createElement('span');badge.id='auraNotificationsBadge';badge.style.cssText='display:none;float:right;min-width:20px;padding:1px 6px;border-radius:999px;background:#ff5f86;color:#fff;font-size:.68rem;text-align:center';button.append(badge);
    button.onclick=async()=>{ensureDrawer().classList.add('open');await loadNotifications()};
    foot.prepend(button);return button;
  }

  function ensureDrawer(){
    let drawer=$('auraNotificationsDrawer');if(drawer)return drawer;
    drawer=document.createElement('div');drawer.id='auraNotificationsDrawer';drawer.className='drawer';
    drawer.innerHTML=`<div style="display:flex;align-items:center;gap:8px"><div style="flex:1"><b>Aura Notifications</b><div class="muted" style="font-size:.72rem">Task results, research completions and items that need attention</div></div><button class="btn" id="auraNotificationsClose">✕</button></div><div style="display:flex;gap:6px;margin:12px 0"><button class="mini" id="auraNotificationsRefresh">Refresh</button><button class="mini" id="auraNotificationsReadAll">Mark all read</button></div><div id="auraNotificationsRows"></div>`;
    document.body.append(drawer);
    $('auraNotificationsClose').onclick=()=>drawer.classList.remove('open');
    $('auraNotificationsRefresh').onclick=loadNotifications;
    $('auraNotificationsReadAll').onclick=async()=>{try{await request(`${api}/notifications/read-all`,{method:'POST',body:'{}'});await loadNotifications();toast('Notifications marked read.')}catch(e){toast(e.message,true)}};
    drawer.addEventListener('click',async event=>{
      const open=event.target.closest('[data-notification-open]');
      const remove=event.target.closest('[data-notification-delete]');
      if(remove){event.stopPropagation();try{await request(`${api}/notifications/${encodeURIComponent(remove.dataset.notificationDelete)}`,{method:'DELETE'});await loadNotifications()}catch(e){toast(e.message,true)}return}
      if(!open)return;
      const id=open.dataset.notificationOpen,thread=open.dataset.thread||'';
      try{await request(`${api}/notifications/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({read:true})});if(thread&&typeof openThread==='function'){drawer.classList.remove('open');await openThread(thread)}await refreshBadge()}catch(e){toast(e.message,true)}
    });
    return drawer;
  }

  function render(rows){
    const target=$('auraNotificationsRows');if(!target)return;
    target.innerHTML=rows.length?rows.map(n=>`<div class="mem" data-notification-open="${esc(n.id)}" data-thread="${esc(n.thread_id||'')}" style="cursor:pointer;${n.unread?'border-color:#9b70ff66;background:#9b70ff0d':''}"><div style="display:flex;align-items:flex-start;gap:8px"><div style="flex:1"><b>${n.unread?'● ':''}${esc(n.title)}</b><div class="muted" style="font-size:.66rem;text-transform:capitalize">${esc(n.kind||'notification')} · ${esc(when(n.created_at))}</div></div><button class="mini" data-notification-delete="${esc(n.id)}">Delete</button></div><div style="font-size:.76rem;line-height:1.45;white-space:pre-wrap;margin-top:6px">${esc(n.body||'')}</div>${n.thread_id?'<div class="muted" style="font-size:.65rem;margin-top:6px">Open originating Aura conversation →</div>':''}</div>`).join(''):'<p class="muted">No Aura notifications yet.</p>';
  }

  function setBadge(count){const badge=$('auraNotificationsBadge');if(!badge)return;const n=Number(count||0);badge.textContent=n>99?'99+':String(n);badge.style.display=n>0?'inline-block':'none'}
  async function refreshBadge(){try{const data=await request(`${api}/notifications?unread_only=true&limit=1`);setBadge(data.unread_count)}catch(_){}}
  async function loadNotifications(){try{const data=await request(`${api}/notifications?limit=100`);render(data.notifications||[]);setBadge(data.unread_count)}catch(e){toast(e.message,true)}}

  ensureButton();refreshBadge();
  window.setInterval(()=>{if(document.visibilityState==='visible')refreshBadge()},60000);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshBadge()});
})();
"""


@router.get('/aura-intelligence/notifications-ui.js')
def notifications_ui_script():
    return Response(content=NOTIFICATIONS_SCRIPT, media_type='application/javascript', headers={'Cache-Control':'no-store'})


__all__=['router','NOTIFICATIONS_SCRIPT']
