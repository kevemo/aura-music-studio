from __future__ import annotations

import os
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .compute_capabilities import compatibility
from .compute_nodes import ComputeNodeRegistry
from .owner_auth import owner_authorized
from .public_address import PublicAddressManager

router = APIRouter()
registry = ComputeNodeRegistry()

CSS = """
body{font-family:system-ui,sans-serif;background:#08040c;color:#fff;margin:0}.wrap{max-width:1100px;margin:auto;padding:28px}.card{background:#170d20;border:1px solid #563364;border-radius:18px;padding:20px;margin:16px 0}.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.muted{color:#cdbfd4}.gold{color:#f0ca72}.good{color:#72dfa0}.bad{color:#ff91a1}.warn{color:#ffd07a}.pill{border:1px solid #6f456f;border-radius:999px;padding:5px 9px}button,.btn{background:#e7b953;color:#160b18;border:0;border-radius:10px;padding:10px 14px;font-weight:800;text-decoration:none;cursor:pointer}button.danger{background:#672638;color:#fff}input{background:#0b0610;color:#fff;border:1px solid #51305d;border-radius:9px;padding:10px}code{word-break:break-all;color:#ffe29a}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


def _authorized(request: Request) -> bool:
    return owner_authorized(request)


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESP Compute Nodes — {escape(PRODUCT_FULL_NAME)}</title><style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>")


def _coordinator_url() -> str:
    configured = (os.getenv("LSS_PUBLIC_BASE_URL") or "").strip()
    if configured and configured.lower() not in {"auto", "automatic"} and not configured.startswith("http://127.0.0.1"):
        return configured.rstrip("/")
    status = PublicAddressManager().read_status()
    return (status.get("recommended_url") or configured or "http://127.0.0.1:8000").rstrip("/")


@router.get("/owner/compute-nodes", response_class=HTMLResponse)
def compute_nodes(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    nodes = registry.list_nodes(stale_after_seconds=max(60, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")) * 4))
    cards = []
    compatible_online = 0
    for node in nodes:
        online = bool(node.get("online"))
        version = compatibility(node)
        if online and version["compatible"]:
            compatible_online += 1
        status_class = "good" if online else "bad"
        compat_class = "good" if version["compatible"] else "warn"
        caps = ", ".join(node.get("capabilities") or []) or "none"
        hardware = node.get("hardware") or {}
        gpu = hardware.get("nvidia_gpus") or []
        gpu_text = " · ".join(gpu) if isinstance(gpu, list) and gpu else "No NVIDIA GPU reported"
        compat_text = "Ready for jobs" if version["compatible"] else (version.get("reason") or "Version incompatible")
        revoke = "" if node.get("status") == "revoked" else f"<form method='post' action='/owner/compute-nodes/{escape(node['id'], quote=True)}/revoke'><button class='danger'>Revoke node</button></form>"
        cards.append(f"""<div class='card'><div class='row'><div><h3>{escape(node['name'])}</h3><div class='{status_class}'>{'ONLINE' if online else 'OFFLINE / STALE'}</div></div><span class='pill'>{escape(str(node.get('status') or 'active').upper())}</span></div><p><b>Capabilities:</b> {escape(caps)}</p><p class='muted'>{escape(gpu_text)}</p><p class='{compat_class}'><b>Version:</b> {escape(str(version.get('node_version') or 'unknown'))} · {escape(compat_text)}</p><p class='muted'>Last seen: {escape(str(node.get('last_seen_at') or 'never'))}</p><code>{escape(node['id'])}</code><div style='margin-top:14px'>{revoke}</div></div>""")
    nodes_html = "".join(cards) if cards else "<div class='card'><p class='muted'>No ESP compute nodes are enrolled yet.</p></div>"
    summary = registry.summary()
    body = f"""<div class='row'><div><div class='gold'><b>ESP COMPUTE FABRIC</b></div><h1>Aura Compute Nodes</h1><p class='muted'>Add ESP-controlled PCs/GPUs without giving them the member database or public inbound ports.</p></div><a class='btn' href='/owner/dashboard'>← Owner dashboard</a></div>
<div class='grid'><div class='card'><b>Registered</b><h2>{summary['registered']}</h2></div><div class='card'><b>Compatible + online</b><h2>{compatible_online}</h2></div><div class='card'><b>Worker inbound ports</b><h2>0</h2><span class='muted'>Nodes connect outbound</span></div></div>
<div class='card'><h2>Version safety</h2><p class='muted'>By default Aura only leases work to nodes running the exact same ESP Live Sound Studio version as the coordinator. This protects project/session formats during rolling upgrades.</p></div>
<div class='card'><h2>Enroll another ESP machine</h2><p class='muted'>Create a one-time enrollment token. It expires automatically and becomes unusable as soon as one node exchanges it.</p><form method='post' action='/owner/compute-nodes/enrollments'><input name='label' value='ESP Compute Node' maxlength='120'><input name='ttl_minutes' type='number' min='5' max='1440' value='30'><button>Create enrollment code</button></form></div>
<h2>Nodes</h2>{nodes_html}"""
    return _page(body)


@router.post("/owner/compute-nodes/enrollments", response_class=HTMLResponse)
def create_enrollment(request: Request, label: str = Form("ESP Compute Node"), ttl_minutes: int = Form(30)):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    item = registry.create_enrollment(label=label, ttl_minutes=ttl_minutes)
    coordinator = _coordinator_url()
    command = f"aura node-enroll --coordinator {coordinator}"
    return _page(f"""<div class='card'><div class='gold'><b>ONE-TIME ESP NODE ENROLLMENT</b></div><h1>Enrollment code created</h1><p>This code expires at <b>{escape(item['expires_at'])}</b> and is invalid after one successful enrollment.</p><p class='muted'>On the new ESP machine, install/clone Live Sound Studio and run:</p><pre><code>{escape(command)}</code></pre><p>The command securely prompts for this enrollment token rather than requiring it in shell history:</p><pre><code>{escape(item['token'])}</code></pre><p class='muted'>Coordinator: {escape(coordinator)}</p><a class='btn' href='/owner/compute-nodes'>Back to nodes</a></div>""")


@router.post("/owner/compute-nodes/{node_id}/revoke")
def revoke_node(node_id: str, request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    registry.revoke(node_id)
    return RedirectResponse("/owner/compute-nodes", status_code=303)
