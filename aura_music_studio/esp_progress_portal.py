from __future__ import annotations

from html import escape

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_niche import EspNicheStore, require_esp_hub_member
from .esp_progress import EspProgressStore, save_progress_upload

router = APIRouter()
progress = EspProgressStore()
niches = EspNicheStore()


CSS = """
:root{--bg:#07050d;--panel:#15101d;--line:#ffffff20;--text:#fff;--muted:#c8bfd2;--gold:#e9bd65;--purple:#a06dff;--green:#74d89c}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#4a195d,transparent 32%),#07050d;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1080px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}.card{border:1px solid var(--line);border-radius:19px;background:#120d1aeb;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{border:1px solid var(--line);border-radius:13px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.3rem}.muted{color:var(--muted)}.gold{color:var(--gold)}label{display:block;font-weight:800;margin:8px 0 5px}input,select,textarea{width:100%;border:1px solid var(--line);background:#09070f;color:#fff;border-radius:11px;padding:11px}textarea{min-height:100px}.btn,button{border:0;border-radius:11px;padding:10px 14px;font-weight:850;background:linear-gradient(115deg,var(--gold),var(--purple));color:#150c1c;text-decoration:none;cursor:pointer;display:inline-block}.secondary{background:#ffffff0b;color:#fff;border:1px solid var(--line)}.good{color:var(--green)}ul{line-height:1.55}.small{font-size:.82rem}@media(max-width:760px){.grid,.metrics{grid-template-columns:1fr}}
"""


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Creator Progress</title><style>{CSS}</style></head><body><main class='wrap'>{body}</main></body></html>")


def _metric(name: str, value) -> str:
    return f"<div class='metric'><small class='muted'>{escape(name)}</small><b>{escape(str(value))}</b></div>"


@router.get("/command-center/progress", response_class=HTMLResponse, include_in_schema=False)
def creator_progress(request: Request):
    member, membership = require_esp_hub_member(request)
    profile = niches.get(member.user_id)
    if profile is None:
        return RedirectResponse("/command-center/niche", status_code=303)
    definition = profile["catalog"]
    rows = progress.list_for_user(member.user_id, 40)
    latest = rows[0] if rows else None
    latest_metrics = "".join(_metric(k.replace("_", " ").title(), v) for k, v in (latest or {}).get("metrics", {}).items())
    latest_guidance = "".join(f"<li>{escape(item)}</li>" for item in (latest or {}).get("aura_guidance", []))
    history = "".join(
        f"<div class='card'><div class='row'><div><b>{escape(row['kind'].upper())}</b> · {escape(row.get('period_label') or 'Progress update')}</div><small class='muted'>{escape(row['created_at'][:16].replace('T',' '))}</small></div><div class='metrics' style='margin-top:10px'>{''.join(_metric(k.replace('_',' ').title(), v) for k,v in list(row.get('metrics',{}).items())[:8])}</div>{f"<p class='muted'>{escape(row.get('notes') or '')}</p>" if row.get('notes') else ''}</div>"
        for row in rows
    ) or "<div class='card'><p class='muted'>No LIVE or video analysis has been added yet.</p></div>"

    body = f"""<div class='top'><div><div class='gold'><b>ESP CREATOR PROGRESS</b></div><h1>{escape(definition['icon'])} {escape(definition['title'])}</h1><p class='muted'>Upload or enter TikTok LIVE/video analysis so Aura can track progress using your selected niche and ESP training context.</p></div><div><a class='btn secondary' href='/command-center'>ESP Hub</a> <a class='btn secondary' href='/command-center/niche'>Niche Select</a></div></div>
<div class='card'><h2>Add performance analysis</h2><form method='post' action='/command-center/progress' enctype='multipart/form-data'><div class='grid'><div><label>Analysis type</label><select name='kind'><option value='live'>TikTok LIVE analysis</option><option value='video'>TikTok video analysis</option></select></div><div><label>Period / label</label><input name='period_label' placeholder='Example: 24 Aug evening LIVE'></div></div><div class='metrics' style='margin-top:8px'><div><label>Views</label><input type='number' step='1' min='0' name='views'></div><div><label>Duration minutes</label><input type='number' step='.1' min='0' name='duration_minutes'></div><div><label>Average watch seconds</label><input type='number' step='.1' min='0' name='avg_watch_seconds'></div><div><label>Completion rate %</label><input type='number' step='.1' min='0' max='100' name='completion_rate'></div><div><label>Peak viewers</label><input type='number' step='1' min='0' name='peak_viewers'></div><div><label>New followers</label><input type='number' step='1' min='0' name='new_followers'></div><div><label>Comments</label><input type='number' step='1' min='0' name='comments'></div><div><label>Shares</label><input type='number' step='1' min='0' name='shares'></div><div><label>Saves</label><input type='number' step='1' min='0' name='saves'></div><div><label>Diamonds</label><input type='number' step='1' min='0' name='diamonds'></div></div><label>Creator notes / what happened</label><textarea name='notes' placeholder='What worked? What felt weak? What did viewers respond to?'></textarea><label>Upload analytics export or screenshot (optional)</label><input type='file' name='analysis_file' accept='.csv,.json,.txt,.pdf,.png,.jpg,.jpeg,.webp'><p class='muted small'>Accepted: CSV, JSON, TXT, PDF or analytics screenshots up to 10 MB. Files remain inside the ESP member's private progress area.</p><button type='submit'>Save & get Aura guidance</button></form></div>
{f"<div class='card'><h2>Latest Aura progress guidance</h2><div class='metrics'>{latest_metrics}</div><ul>{latest_guidance}</ul></div>" if latest else ''}
<h2>Progress history</h2>{history}"""
    return _page(body)


@router.post("/command-center/progress", include_in_schema=False)
async def save_creator_progress(
    request: Request,
    kind: str = Form(...),
    period_label: str = Form(""),
    views: str = Form(""),
    duration_minutes: str = Form(""),
    avg_watch_seconds: str = Form(""),
    completion_rate: str = Form(""),
    peak_viewers: str = Form(""),
    new_followers: str = Form(""),
    comments: str = Form(""),
    shares: str = Form(""),
    saves: str = Form(""),
    diamonds: str = Form(""),
    notes: str = Form(""),
    analysis_file: UploadFile | None = File(None),
):
    member, _membership = require_esp_hub_member(request)
    if niches.get(member.user_id) is None:
        return RedirectResponse("/command-center/niche", status_code=303)

    metrics = {
        "views": views,
        "duration_minutes": duration_minutes,
        "avg_watch_seconds": avg_watch_seconds,
        "completion_rate": completion_rate,
        "peak_viewers": peak_viewers,
        "new_followers": new_followers,
        "comments": comments,
        "shares": shares,
        "saves": saves,
        "diamonds": diamonds,
    }
    upload_name = upload_path = upload_type = None
    if analysis_file and analysis_file.filename:
        content = await analysis_file.read(10 * 1024 * 1024 + 1)
        upload_name, upload_path = save_progress_upload(member.user_id, analysis_file.filename, content)
        upload_type = analysis_file.content_type
    progress.add(
        member.user_id,
        kind=kind,
        period_label=period_label,
        metrics=metrics,
        notes=notes,
        upload_name=upload_name,
        upload_path=upload_path,
        upload_content_type=upload_type,
    )
    return RedirectResponse("/command-center/progress", status_code=303)
