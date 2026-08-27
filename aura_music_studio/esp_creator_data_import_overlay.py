from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import esp_creator_data_import as base

router = APIRouter(tags=["ESP Creator Data Import Browser"])
_REVIEW_PATH = "/command-center/progress/import/{import_id}"

# Replace the foundation's API-only review page with a human confirmation workflow while
# keeping its parser, persistence and JSON APIs authoritative.
base.router.routes[:] = [
    route
    for route in base.router.routes
    if not (
        getattr(route, "path", None) == _REVIEW_PATH
        and "GET" in (getattr(route, "methods", set()) or set())
    )
]


def _option_list(columns: list[str], selected: str = "") -> str:
    options = ["<option value=''>Do not import</option>"]
    for column in columns:
        is_selected = " selected" if column == selected else ""
        options.append(
            f"<option value='{escape(column, quote=True)}'{is_selected}>{escape(column)}</option>"
        )
    return "".join(options)


def _metric_label(metric: str) -> str:
    labels = {
        "views": "Views",
        "duration_minutes": "Duration minutes",
        "avg_watch_seconds": "Average watch seconds",
        "completion_rate": "Completion rate %",
        "peak_viewers": "Peak viewers",
        "new_followers": "New followers",
        "comments": "Comments",
        "shares": "Shares",
        "saves": "Saves",
        "diamonds": "Diamonds",
    }
    return labels.get(metric, metric.replace("_", " ").title())


@router.get(_REVIEW_PATH, response_class=HTMLResponse, include_in_schema=False)
def review_data_import_page(import_id: str, request: Request):
    member = base._creator(request)
    try:
        item = base.data_imports.get(member.user_id, import_id)
    except KeyError as exc:
        raise HTTPException(404, "Creator data import not found") from exc

    columns = item.get("columns") or []
    detected = item.get("detected_mapping") or {}
    mapping_fields = "".join(
        "<div><label>"
        + escape(_metric_label(metric))
        + "</label><select name='map_"
        + escape(metric, quote=True)
        + "'>"
        + _option_list(columns, detected.get(metric, ""))
        + "</select></div>"
        for metric in base.STANDARD_METRICS
    )
    period_options = "<option value=''>Use default label</option>" + "".join(
        f"<option value='{escape(column, quote=True)}'>{escape(column)}</option>"
        for column in columns
    )
    detected_text = ", ".join(
        f"{_metric_label(metric)} ← {column}" for metric, column in detected.items()
    ) or "No standard metrics were auto-detected. Choose the source columns manually below."

    if item.get("status") == "staged":
        controls = f"""
<section class='card'>
  <h2>Confirm what enters Creator Progress</h2>
  <p class='muted'>Pulsar has suggested mappings where the source headings are recognisable. Review every field. Blank selections are ignored.</p>
  <form method='post' action='/command-center/progress/import/{escape(import_id, quote=True)}/confirm'>
    <div class='grid'>
      <div><label>Progress type</label><select name='kind' required><option value='live'>TikTok LIVE</option><option value='video'>TikTok video</option></select></div>
      <div><label>Period/date source column</label><select name='period_column'>{period_options}</select></div>
      <div><label>Fallback period label</label><input name='default_period_label' maxlength='160' value='Imported analytics'></div>
    </div>
    <h3>Metric mapping</h3><div class='grid'>{mapping_fields}</div>
    <label>Notes added to every imported row</label><textarea name='notes' maxlength='2000' placeholder='Optional context about this export or reporting period'></textarea>
    <div class='row' style='margin-top:12px'><button class='primary' type='submit'>Confirm & import mapped rows</button></div>
  </form>
  <form method='post' action='/command-center/progress/import/{escape(import_id, quote=True)}/reject' style='margin-top:10px'>
    <button class='danger' type='submit'>Reject this staged import</button>
  </form>
</section>"""
    else:
        imported_count = len(item.get("imported_submission_ids") or [])
        controls = (
            "<section class='card'><h2>Import resolved</h2>"
            f"<p class='muted'>Status: <b>{escape(str(item.get('status') or 'resolved'))}</b>. "
            f"Imported progress rows: <b>{imported_count}</b>. This staged file cannot be imported again.</p>"
            "<a class='btn primary' href='/command-center/progress'>Open Creator Progress</a></section>"
        )

    html = (
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><title>Review Creator Import</title>"
        f"<style>{base.CSS}textarea{{width:100%;min-height:100px;border:1px solid var(--line);border-radius:10px;padding:10px;background:#080610;color:#fff;margin:5px 0}}label{{display:block;font-weight:800;margin-top:8px}}.danger{{background:#3a1520;color:#fff}}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Import Preview · Human Confirmation</div>"
        f"<h1>{escape(item['upload_name'])}</h1><p class='muted'>{item['row_count']} rows · {escape(item['source_format'].upper())} · status {escape(item['status'])}</p></div>"
        "<div><a class='btn' href='/command-center/progress/import'>All imports</a> <a class='btn' href='/command-center/progress'>Creator Progress</a></div></div>"
        f"<section class='card'><h2>Detected mapping</h2><p>{escape(detected_text)}</p><p class='muted'>Detection is a suggestion only. Nothing is written until you confirm the mapping below.</p></section>"
        f"{base._preview_table(item)}{controls}"
        "<section class='card'><b>Privacy boundary</b><p class='muted'>The original export remains in your private ESP progress storage. Raw server paths are not exposed to this page or the member API.</p></section>"
        "</main></body></html>"
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.post(f"{_REVIEW_PATH}/confirm", include_in_schema=False)
def confirm_data_import_page(
    import_id: str,
    request: Request,
    kind: str = Form(...),
    period_column: str = Form(""),
    default_period_label: str = Form("Imported analytics"),
    notes: str = Form(""),
    map_views: str = Form(""),
    map_duration_minutes: str = Form(""),
    map_avg_watch_seconds: str = Form(""),
    map_completion_rate: str = Form(""),
    map_peak_viewers: str = Form(""),
    map_new_followers: str = Form(""),
    map_comments: str = Form(""),
    map_shares: str = Form(""),
    map_saves: str = Form(""),
    map_diamonds: str = Form(""),
):
    member = base._creator(request)
    mapping = {
        "views": map_views,
        "duration_minutes": map_duration_minutes,
        "avg_watch_seconds": map_avg_watch_seconds,
        "completion_rate": map_completion_rate,
        "peak_viewers": map_peak_viewers,
        "new_followers": map_new_followers,
        "comments": map_comments,
        "shares": map_shares,
        "saves": map_saves,
        "diamonds": map_diamonds,
    }
    mapping = {metric: column for metric, column in mapping.items() if str(column).strip()}
    try:
        body = base.ImportConfirm(
            kind=kind,
            mapping=mapping,
            period_column=period_column,
            default_period_label=default_period_label,
            notes=notes,
        )
        base.data_imports.confirm(member.user_id, import_id, body)
    except KeyError as exc:
        raise HTTPException(404, "Creator data import not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/command-center/progress", status_code=303)


@router.post(f"{_REVIEW_PATH}/reject", include_in_schema=False)
def reject_data_import_page(import_id: str, request: Request):
    member = base._creator(request)
    try:
        base.data_imports.reject(member.user_id, import_id)
    except KeyError as exc:
        raise HTTPException(404, "Creator data import not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/command-center/progress/import", status_code=303)


# Browser routes are registered first so the richer review page replaces the foundation's
# removed API-only page. Extend with the already-constructed foundation route objects directly
# so all staging/list/JSON API routes are visible deterministically during test collection.
router.routes.extend(list(base.router.routes))


__all__ = ["router"]
