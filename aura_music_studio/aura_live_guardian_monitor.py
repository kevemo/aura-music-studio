from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse


def live_guardian_monitor(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")

    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Guardian Monitor</title><style>
body{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(760px,calc(100% - 28px));margin:auto;padding:28px 0}.card{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.pill{padding:8px 12px;border-radius:999px;background:#ffffff10;border:1px solid #ffffff20;font-weight:900}.muted{color:#bdc6d8}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}.metric{background:#ffffff08;border:1px solid #ffffff14;border-radius:14px;padding:14px}.big{font-size:1.8rem;font-weight:950}.btn{display:inline-block;color:#fff;text-decoration:none;padding:10px 12px;border-radius:10px;border:1px solid #ffffff22}.ready{color:#8ef0b0}.review{color:#ffd483}.attention{color:#ffbc7a}.critical{color:#ff7a9f}@media(max-width:560px){.grid{grid-template-columns:1fr}}
</style></head><body><main class='wrap'><div class='card'><div class='row'><div><div class='muted'>Aura AI · Self-hosted LIVE safety</div><h1 style='margin:.2em 0'>Guardian Monitor</h1></div><span id='state' class='pill'>Loading…</span></div><p id='message' class='muted'>Reading the Command Center's local Guardian state.</p><div class='grid'><div class='metric'><div class='muted'>Pending review</div><div id='pending' class='big'>—</div></div><div class='metric'><div class='muted'>Critical escalations</div><div id='critical' class='big'>—</div></div><div class='metric'><div class='muted'>Audit integrity</div><div id='audit' class='big'>—</div></div><div class='metric'><div class='muted'>Provider execution</div><div id='provider' class='big'>Fail-closed</div></div></div><p class='muted'>This page refreshes only from the same-origin Aura server. It uses no external realtime provider, analytics service, CDN script or third-party browser SDK.</p><div class='row'><a class='btn' href='/live-guardian'>Guardian</a><a class='btn' href='/live-guardian/readiness'>Readiness</a><a class='btn' href='/live-guardian/review'>Human Review</a></div></div></main><script>
const state=document.getElementById('state'),message=document.getElementById('message'),pending=document.getElementById('pending'),critical=document.getElementById('critical'),audit=document.getElementById('audit'),provider=document.getElementById('provider');
async function refresh(){try{const response=await fetch('/live-guardian/status',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});if(!response.ok)throw new Error('status unavailable');const data=await response.json();state.textContent=String(data.safety_state||'attention').toUpperCase();state.className='pill '+String(data.safety_state||'attention');message.textContent=String(data.message||'Guardian status unavailable.');pending.textContent=String(data.pending_reviews??'—');critical.textContent=String(data.critical_escalations??'—');audit.textContent=data.audit_integrity_ok?'Verified':'Attention';provider.textContent=data.provider_execution_ready?'Runtime verified':'Fail-closed';}catch(error){state.textContent='OFFLINE';state.className='pill attention';message.textContent='Aura Guardian status is temporarily unavailable. No provider authority is inferred.';pending.textContent='—';critical.textContent='—';audit.textContent='Attention';provider.textContent='Fail-closed';}}
refresh();setInterval(refresh,3000);
</script></body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
        },
    )


__all__ = ["live_guardian_monitor"]
