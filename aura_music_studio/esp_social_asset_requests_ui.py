from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .branding import ENDORSEMENT
from .esp_niche import require_esp_social_member
from .social_management import SocialHouseStore

router = APIRouter(tags=["ESP Social Asset Requests UI"])


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


def _targets() -> list[dict]:
    store = SocialHouseStore()
    rows: list[dict] = []
    for index in store.list_spaces():
        try:
            house = store.load(str(index.get("id") or ""))
        except Exception:
            continue
        rows.append({"space_id": house.id, "space_name": house.name, "content_id": None, "label": f"{house.name} · General asset request"})
        for content in house.content:
            rows.append({
                "space_id": house.id,
                "space_name": house.name,
                "content_id": content.id,
                "label": f"{house.name} · {content.title} · {content.status}",
            })
    return rows


CSS = r"""
:root{--line:#ffffff1d;--muted:#bbc3d5;--gold:#efcc77;--violet:#9b6fff;--red:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 7% 0,#3d1769,transparent 30%),radial-gradient(circle at 95% 0,#123f73,transparent 24%),#03040a;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto}a{color:inherit;text-decoration:none}.nav{position:sticky;top:0;z-index:10;border-bottom:1px solid var(--line);background:#05070dec}.navin{min-height:70px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--gold);font-size:.65rem;text-transform:uppercase}.hero{padding:45px 0 20px}.eyebrow{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5rem);line-height:.94;letter-spacing:-.055em;margin:.15em 0 .2em}.lead,.muted{color:var(--muted);line-height:1.55}.layout{display:grid;grid-template-columns:.82fr 1.18fr;gap:12px;padding-bottom:52px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#11162a,#080b15);padding:15px}.stack{display:grid;gap:8px}.field{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#060912;color:#fff;font:inherit}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1e}.danger{border-color:#ff90a455;color:var(--red)}.request,.asset{border:1px solid var(--line);border-radius:13px;padding:11px;margin:8px 0;background:#ffffff04}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.68rem;margin:2px}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0}.notice.show{display:block}.secret{word-break:break-all;border:1px solid #efcc7750;border-radius:11px;padding:10px;background:#161108;margin:8px 0}.footer{border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:.8rem}@media(max-width:850px){.layout{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/social/asset-requests',seed=JSON.parse(document.getElementById('seed').textContent),$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff90a455':'';clearTimeout(window._n);window._n=setTimeout(()=>n.className='notice',6000)}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}function fill(){const sel=$('target');sel.innerHTML=seed.length?seed.map((r,i)=>`<option value="${i}">${esc(r.label)}</option>`).join(''):'<option value="">No Social House yet</option>'}function render(rows){$('requests').innerHTML=rows.length?rows.map(r=>`<div class="request"><b>${esc(r.title)}</b><div><span class="pill">${r.asset_count} asset${r.asset_count===1?'':'s'}</span>${r.revoked_at?'<span class="pill">REVOKED</span>':''}</div><div class="muted" style="font-size:.72rem">${esc(r.space_id)}${r.content_id?' · '+esc(r.content_id):''}<br>Expires ${esc(r.expires_at)}</div><div style="margin-top:7px"><button class="btn" onclick="assets('${esc(r.id)}')">View received assets</button>${r.revoked_at?'':` <button class="btn danger" onclick="revoke('${esc(r.id)}')">Revoke</button>`}</div><div id="assets_${esc(r.id)}"></div></div>`).join(''):'<p class="muted">No asset requests yet.</p>'}async function load(){try{render((await req(API)).requests||[])}catch(e){note(e.message,true)}}async function create(){const i=Number($('target').value);if(!Number.isInteger(i)||!seed[i])return note('Choose a Social House target.',true);const t=seed[i],title=$('title').value.trim()||'Upload requested assets',instructions=$('instructions').value.trim(),expires_hours=Number($('expires').value||168);try{const d=await req(API,{method:'POST',body:JSON.stringify({space_id:t.space_id,content_id:t.content_id,title,instructions,expires_hours})}),absolute=new URL(d.url,location.origin).href;$('secret').style.display='block';$('secret').innerHTML=`<b>Copy this upload link now — the raw token is not stored:</b><div class="secret">${esc(absolute)}</div><button class="btn primary" id="copyAsset">Copy link</button>`;document.getElementById('copyAsset').onclick=async()=>{try{await navigator.clipboard.writeText(absolute);note('Asset request link copied.')}catch(_){note('Copy failed. Select the URL manually.',true)}};await load()}catch(e){note(e.message,true)}}async function assets(id){try{const d=await req(`${API}/${encodeURIComponent(id)}/assets`),box=$(`assets_${id}`);box.innerHTML=(d.assets||[]).length?(d.assets||[]).map(a=>`<div class="asset"><b>${esc(a.original_name)}</b><div class="muted" style="font-size:.72rem">${Math.ceil((a.size_bytes||0)/1024)} KB · ${esc(a.media_type)} · uploaded by ${esc(a.uploader_name)} · ${esc(a.status)}</div><div class="muted" style="font-size:.68rem">Rights confirmed ${esc(a.rights_confirmed_at)}</div></div>`).join(''):'<p class="muted">No uploads yet.</p>'}catch(e){note(e.message,true)}}async function revoke(id){if(!confirm('Revoke this asset request link?'))return;try{await req(`${API}/${encodeURIComponent(id)}`,{method:'DELETE'});note('Asset request revoked.');await load()}catch(e){note(e.message,true)}}$('create').onclick=create;fill();load();
"""


@router.get("/command-center/social/asset-requests", response_class=HTMLResponse, include_in_schema=False)
def asset_requests_page(request: Request):
    _member(request)
    seed = json.dumps(_targets(), ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Asset Requests</title><style>{CSS}</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/command-center/social'>Elevate Souls Productions<small>Asset Request Manager</small></a><div><a class='btn' href='/command-center/social/review-links'>Review Links</a> <a class='btn' href='/command-center/social/approvals'>Approval Inbox</a> <a class='btn' href='/command-center/social'>Social House</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>Rights-confirmed media collection</div><h1>Request source media <span style='color:var(--gold)'>without sharing the workspace.</span></h1><p class='lead'>Create an expiring upload link for one ESP Social House or content item. Received files are quarantined as pending review and cannot publish or enter a Creative House project automatically.</p><div id='notice' class='notice'></div></section><section class='layout'><div class='card'><div class='eyebrow'>Create request</div><div class='stack'><select id='target' class='field'></select><input id='title' class='field' maxlength='300' placeholder='What should they upload?'><textarea id='instructions' class='field' maxlength='3000' placeholder='Instructions / requested assets'></textarea><select id='expires' class='field'><option value='24'>Expires in 24 hours</option><option value='72'>3 days</option><option value='168' selected>7 days</option><option value='336'>14 days</option><option value='720'>30 days</option></select><button id='create' class='btn primary'>Create asset request link</button><div id='secret' style='display:none'></div></div></div><div class='card'><div class='eyebrow'>Requests & received assets</div><div id='requests'><p class='muted'>Loading…</p></div></div></section></main><footer class='footer'><div class='wrap'>{ENDORSEMENT}</div></footer><script id='seed' type='application/json'>{seed}</script><script>{SCRIPT}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "SCRIPT"]
