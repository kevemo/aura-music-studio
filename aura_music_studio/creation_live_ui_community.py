from __future__ import annotations

_PATCHED = False


def _community_function() -> str:
    return r"""
+ async function community(){
+  const box=$('clCommunity');if(!box)return;box.replaceChildren();
+  const pid=projectName();if(!pid)return;
+  try{
+   const d=await req(`/creation-live/projects/${encodeURIComponent(pid)}/community`);
+   const title=document.createElement('b');title.textContent='Shared Sky community';box.append(title);
+   if(!d.available){const p=document.createElement('div');p.textContent=d.truth_note||`Community: ${d.state||'unavailable'}`;p.style.color='#bdc7d8';p.style.fontSize='.78rem';box.append(p);return}
+   const stats=document.createElement('div');stats.textContent=`${Number(d.viewer_count||0)} watching · ${d.broadcast?.title||'LIVE'}`;stats.style.fontSize='.78rem';box.append(stats);
+   const reactions=document.createElement('div');const rs=Object.entries(d.reactions||{}).filter(([,v])=>Number(v)>0).map(([k,v])=>`${k} ${v}`).join(' · ');reactions.textContent=rs||'No reaction activity yet.';reactions.style.fontSize='.72rem';reactions.style.color='#bdc7d8';box.append(reactions);
+   const chat=document.createElement('div');chat.setAttribute('aria-label','Recent Shared Sky chat');chat.style.cssText='margin-top:7px;max-height:160px;overflow:auto;border-top:1px solid #ffffff18;padding-top:5px';
+   const messages=Array.isArray(d.chat)?d.chat.slice(-20):[];
+   if(!messages.length){const empty=document.createElement('div');empty.textContent='No recent internal chat messages.';empty.style.fontSize='.72rem';chat.append(empty)}
+   messages.forEach(m=>{const row=document.createElement('div');row.style.fontSize='.72rem';const who=document.createElement('b');who.textContent=String(m.sender_user_id||'viewer').slice(0,12);const body=document.createElement('span');body.textContent=m.deleted?' [removed] ':` ${m.body||''}`;row.append(who,body);chat.append(row)});box.append(chat);
+   const boundaries=document.createElement('div');boundaries.textContent='Community is display-only here. Viewer activity never edits the project automatically.';boundaries.style.cssText='margin-top:7px;font-size:.68rem;color:#8fe1b4';box.append(boundaries);
+  }catch(e){const p=document.createElement('div');p.textContent='Community state unavailable: '+e.message;p.style.cssText='font-size:.72rem;color:#ff9eae';box.append(p)}
+ }
""".replace("\n+", "\n").lstrip("+")


def harden_community_ui(script: str) -> str:
    marker = "async function community(){"
    if marker in script:
        return script

    script = script.replace(
        '<div id="clStatus" style="margin-top:10px"></div>',
        '<div id="clStatus" style="margin-top:10px"></div><div id="clCommunity" style="margin-top:12px;border:1px solid #ffffff20;border-radius:12px;padding:10px" aria-live="polite"></div>',
    )
    script = script.replace(" async function preview(){", _community_function() + " async function preview(){")
    script = script.replace(
        "$('clDetach').onclick=detach;}",
        "$('clDetach').onclick=detach;community();}",
    )
    script = script.replace(
        "state.selected=data.source;state.status=data;renderStatus();msg(data.transport?.available?",
        "state.selected=data.source;state.status=data;renderStatus();await community();msg(data.transport?.available?",
    )
    script = script.replace(
        "state.selected=state.status.source;renderStatus()}catch(e)",
        "state.selected=state.status.source;renderStatus();await community()}catch(e)",
    )
    return script


def install_creation_live_community_ui() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from . import creation_live as cl

    cl.LIVE_UI_SCRIPT = harden_community_ui(cl.LIVE_UI_SCRIPT)
    cl.creation_live_community_ui_installed = True
    _PATCHED = True


__all__ = ["harden_community_ui", "install_creation_live_community_ui"]
