from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .branding import ENDORSEMENT
from .esp_niche import require_esp_social_member
from .social_management import SocialHouseStore

router = APIRouter(tags=["ESP Social Review Links UI"])


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


def _content_index() -> list[dict]:
    store = SocialHouseStore()
    rows: list[dict] = []
    for index in store.list_spaces():
        try:
            house = store.load(str(index.get("id") or ""))
        except Exception:
            continue
        for content in house.content:
            rows.append(
                {
                    "space_id": house.id,
                    "space_name": house.name,
                    "content_id": content.id,
                    "content_title": content.title,
                    "status": content.status,
                    "approval_required": content.approval_required,
                    "variant_count": len(content.variants),
                }
            )
    rows.sort(key=lambda row: (row["space_name"].lower(), row["content_title"].lower()))
    return rows


CSS = r"""
:root{--bg:#03040a;--panel:#0d1120;--line:#ffffff1d;--text:#fff;--muted:#b9c1d3;--gold:#efcc77;--violet:#9b6fff;--green:#75dda7;--red:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#3d1769,transparent 30%),radial-gradient(circle at 94% 0,#133e72,transparent 26%),#03040a;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto}a{color:inherit;text-decoration:none}.nav{border-bottom:1px solid var(--line);background:#05070dec;position:sticky;top:0;z-index:10}.navin{min-height:70px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.65rem;text-transform:uppercase;letter-spacing:.08em}.hero{padding:44px 0 20px}.eyebrow{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.1rem);line-height:.94;letter-spacing:-.055em;margin:.15em 0 .2em}.lead,.muted{color:var(--muted);line-height:1.55}.layout{display:grid;grid-template-columns:.85fr 1.15fr;gap:12px;padding-bottom:50px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#11162a,#080b15);padding:15px}.stack{display:grid;gap:8px}.field{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#060912;color:#fff;font:inherit}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1e}.danger{border-color:#ff90a455;color:var(--red)}.link{border:1px solid var(--line);border-radius:13px;padding:11px;margin:8px 0;background:#ffffff04}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.68rem;margin:2px}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0}.notice.show{display:block}.secret{word-break:break-all;border:1px solid #efcc7750;border-radius:11px;padding:10px;background:#161108;margin:8px 0}.footer{border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:.8rem}@media(max-width:850px){.layout{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/social/review-links',seed=JSON.parse(document.getElementById('seed').textContent),$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':'';clearTimeout(window._n);window._n=setTimeout(()=>n.className='notice',6000)}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function fill(){const sel=$('content');sel.innerHTML=seed.length?seed.map(r=>`<option value="${esc(r.space_id)}|${esc(r.content_id)}">${esc(r.space_name)} · ${esc(r.content_title)} · ${esc(r.status)} · ${r.variant_count} variant${r.variant_count===1?'':'s'}</option>`).join(''):'<option value="">No Social House content yet</option>'}function render(rows){$('links').innerHTML=rows.length?rows.map(r=>`<div class="link"><b>${esc(r.space_id)} · ${esc(r.content_id)}</b><div>${(r.scopes||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div><div class="muted" style="font-size:.72rem">Expires ${esc(r.expires_at)}${r.revoked_at?' · REVOKED '+esc(r.revoked_at):''}${r.last_used_at?' · last used '+esc(r.last_used_at):''}</div>${r.revoked_at?'':`<button class="btn danger" onclick="revoke('${esc(r.id)}')">Revoke</button>`}</div>`).join(''):'<p class="muted">No review links created yet.</p>'}async function load(){try{const d=await req(API);render(d.links||[])}catch(e){note(e.message,true)}}async function create(){const raw=$('content').value;if(!raw)return note('Choose content first.',true);const split=raw.indexOf('|'),space_id=raw.slice(0,split),content_id=raw.slice(split+1),scopes=['view'];if($('comment').checked)scopes.push('comment');if($('approve').checked)scopes.push('approve');const expires_hours=Number($('expires').value||168);try{const d=await req(API,{method:'POST',body:JSON.stringify({space_id,content_id,scopes,expires_hours})});const absolute=new URL(d.url,location.origin).href;$('secret').style.display='block';$('secret').innerHTML=`<b>Copy this link now — the raw token is not stored:</b><div class="secret">${esc(absolute)}</div><button class="btn primary" id="copyNow">Copy link</button>`;document.getElementById('copyNow').onclick=async()=>{try{await navigator.clipboard.writeText(absolute);note('Review link copied.')}catch(_){note('Copy failed. Select the URL manually.',true)}};await load()}catch(e){note(e.message,true)}}async function revoke(id){if(!confirm('Revoke this review link?'))return;try{await req(API+'/'+encodeURIComponent(id),{method:'DELETE'});note('Review link revoked.');await load()}catch(e){note(e.message,true)}}$('create').onclick=create;fill();load();
"""


@router.get("/command-center/social/review-links", response_class=HTMLResponse, include_in_schema=False)
def review_links_page(request: Request):
    _member(request)
    rows = _content_index()
    import json

    seed = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Review Links</title><style>{CSS}</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/command-center/social'>Elevate Souls Productions<small>Private Review Link Manager</small></a><div><a class='btn' href='/command-center/social/approvals'>Approval Inbox</a> <a class='btn' href='/command-center/social'>Social House</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Scoped external collaboration</div><h1>Share the content — <span style='color:var(--gold)'>not the workspace.</span></h1><p class='lead'>Create expiring, revocable no-login links for one content item. A link can allow view, comments and optionally approval. It never grants ESP access or external publishing authority.</p><div id='notice' class='notice'></div></section><section class='layout'><div class='card'><div class='eyebrow'>Create link</div><div class='stack'><select id='content' class='field'></select><label class='muted'><input id='comment' type='checkbox' checked> Allow comments</label><label class='muted'><input id='approve' type='checkbox'> Allow approval</label><label class='muted'>Expires after<select id='expires' class='field'><option value='24'>24 hours</option><option value='72'>3 days</option><option value='168' selected>7 days</option><option value='336'>14 days</option><option value='720'>30 days</option></select></label><button id='create' class='btn primary'>Create secure review link</button><div id='secret' style='display:none'></div></div></div><div class='card'><div class='eyebrow'>Existing links</div><div id='links'><p class='muted'>Loading…</p></div></div></section></main><footer class='footer'><div class='wrap'>{ENDORSEMENT}</div></footer><script id='seed' type='application/json'>{seed}</script><script>{SCRIPT}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "SCRIPT"]
