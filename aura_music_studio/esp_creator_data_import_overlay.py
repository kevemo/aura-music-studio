from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import esp_creator_data_import as base
from .esp_creator_import_governance import (
    ImportMappingTemplateCreate,
    ImportProvenanceInput,
    governance,
    router as governance_router,
)

router = APIRouter(tags=["ESP Creator Data Import Browser"])
_REVIEW_PATH = "/command-center/progress/import/{import_id}"
_IMPORT_PATH = "/command-center/progress/import"
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Replace the foundation's browser GET/stage/review routes with human-confirmed governed versions
# while keeping its parser, persistence and private JSON APIs authoritative.
base.router.routes[:] = [
    route
    for route in base.router.routes
    if not (
        (getattr(route, "path", None) == _REVIEW_PATH and "GET" in (getattr(route, "methods", set()) or set()))
        or (getattr(route, "path", None) == _IMPORT_PATH and "GET" in (getattr(route, "methods", set()) or set()))
        or (getattr(route, "path", None) == _IMPORT_PATH and "POST" in (getattr(route, "methods", set()) or set()))
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


def _template_options(user_id: str) -> str:
    options = ["<option value=''>Use conservative auto-detection</option>"]
    for item in governance.list_mappings(user_id):
        options.append(
            "<option value='"
            + escape(item["id"], quote=True)
            + "'>"
            + escape(f"{item['name']} · {item['source_format'].upper()} · {item['kind']}")
            + "</option>"
        )
    return "".join(options)


def _provenance_panel(user_id: str, import_id: str) -> str:
    try:
        item = governance.provenance_for_import(user_id, import_id)
    except KeyError:
        return (
            "<section class='card'><b>Legacy import</b><p class='muted'>This import predates the Chat 9 provenance layer. "
            "Its existing private upload remains authoritative, but no SHA-256 source record was captured at staging time.</p></section>"
        )
    details = [
        ("Provider/source", item.get("provider") or "Not specified"),
        ("Source label", item.get("source_label") or "Not specified"),
        ("Captured", item.get("captured_at") or "Not specified"),
        ("Reporting period", " → ".join(v for v in (item.get("period_start"), item.get("period_end")) if v) or "Not specified"),
        ("Source SHA-256", item.get("source_sha256") or "Unavailable"),
    ]
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>" for label, value in details
    )
    return (
        "<section class='card'><h2>Evidence provenance</h2><div class='scroll'><table>"
        f"{rows}</table></div><p class='muted'>Imported snapshot · not realtime · direct TikTok LIVE Backstage access: no. "
        "The private server upload path is never exposed here.</p></section>"
    )


@router.get(_IMPORT_PATH, response_class=HTMLResponse, include_in_schema=False)
def governed_data_import_page(request: Request):
    member = base._creator(request)
    response = base.data_import_page(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    old_form = (
        "<input type='file' name='data_file' accept='.csv,.json,.xlsx' required><p class='muted'>"
        "CSV, JSON or XLSX up to the existing 10 MB private-progress upload limit. Maximum 500 rows and 50 columns per staged import.</p>"
        "<button class='primary' type='submit'>Upload & preview</button>"
    )
    governed_form = (
        "<div class='grid'>"
        "<div><label>Provider / source</label><input name='provider' maxlength='80' placeholder='TikTok LIVE Studio export'></div>"
        "<div><label>Source label</label><input name='source_label' maxlength='160' placeholder='August Creator analytics'></div>"
        "<div><label>Captured at</label><input name='captured_at' maxlength='80' placeholder='2026-08-31T23:00:00+00:00'></div>"
        "<div><label>Period start</label><input name='period_start' maxlength='40' placeholder='2026-08-01'></div>"
        "<div><label>Period end</label><input name='period_end' maxlength='40' placeholder='2026-08-31'></div>"
        "<div><label>Saved mapping</label><select name='mapping_template_id'>"
        + _template_options(member.user_id)
        + "</select></div></div>"
        "<label>Structured evidence file</label><input type='file' name='data_file' accept='.csv,.json,.xlsx' required>"
        "<p class='muted'>CSV, JSON or XLSX up to 10 MB. Maximum 500 rows and 50 columns per staged import. "
        "The source is SHA-256 fingerprinted so an identical file cannot be accidentally staged twice for the same Creator.</p>"
        "<button class='primary' type='submit'>Upload & preview</button>"
    )
    if old_form in html:
        html = html.replace(old_form, governed_form, 1)
    else:
        html = html.replace("<button class='primary' type='submit'>Upload & preview</button>", governed_form, 1)
    html = html.replace(
        "<b>Nothing imports automatically.</b>",
        "<b>Nothing imports automatically.</b><p class='muted'>Provider, reporting-period and SHA-256 provenance are retained separately from the metric rows so snapshot evidence is never presented as realtime.</p>",
        1,
    )
    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


@router.post(_IMPORT_PATH, include_in_schema=False)
async def governed_stage_data_import_page(
    request: Request,
    data_file: UploadFile = File(...),
    provider: str = Form(""),
    source_label: str = Form(""),
    captured_at: str = Form(""),
    period_start: str = Form(""),
    period_end: str = Form(""),
    mapping_template_id: str = Form(""),
):
    member = base._creator(request)
    content = await data_file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Structured analytics import exceeds the 10 MB limit")
    provenance = ImportProvenanceInput(
        provider=provider,
        source_label=source_label,
        captured_at=captured_at or None,
        period_start=period_start or None,
        period_end=period_end or None,
        mapping_template_id=mapping_template_id or None,
    )
    if mapping_template_id:
        try:
            governance.mapping(member.user_id, mapping_template_id)
        except KeyError as exc:
            raise HTTPException(400, "Saved mapping does not belong to this account") from exc
    try:
        reservation = governance.reserve_source(
            user_id=member.user_id,
            content=content,
            original_filename=data_file.filename or "analytics.csv",
            content_type=data_file.content_type or "",
            provenance=provenance,
        )
    except FileExistsError as exc:
        message = str(exc)
        if message.startswith("duplicate_import:"):
            existing_id = message.split(":", 2)[1]
            return RedirectResponse(f"/command-center/progress/import/{existing_id}?duplicate=1", status_code=303)
        raise HTTPException(409, "This exact source file is already being staged") from exc

    try:
        item = base.data_imports.stage(
            member.user_id,
            data_file.filename or "analytics.csv",
            content,
            data_file.content_type or "",
        )
        governance.attach_import(
            user_id=member.user_id,
            digest=reservation["source_sha256"],
            import_id=item["id"],
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        governance.release_source(user_id=member.user_id, digest=reservation["source_sha256"])
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        governance.release_source(user_id=member.user_id, digest=reservation["source_sha256"])
        raise
    return RedirectResponse(f"/command-center/progress/import/{item['id']}", status_code=303)


@router.get(_REVIEW_PATH, response_class=HTMLResponse, include_in_schema=False)
def review_data_import_page(import_id: str, request: Request):
    member = base._creator(request)
    try:
        item = base.data_imports.get(member.user_id, import_id)
    except KeyError as exc:
        raise HTTPException(404, "Creator data import not found") from exc

    columns = item.get("columns") or []
    detected = dict(item.get("detected_mapping") or {})
    template = None
    try:
        provenance = governance.provenance_for_import(member.user_id, import_id)
        template_id = provenance.get("mapping_template_id")
        template = governance.resolve_mapping(member.user_id, template_id, item["source_format"]) if template_id else None
    except (KeyError, ValueError):
        template = None
    if template:
        detected = {metric: column for metric, column in (template.get("mapping") or {}).items() if column in columns}

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
    period_selected = template.get("period_column", "") if template and template.get("period_column") in columns else ""
    period_options = "<option value=''>Use default label</option>" + "".join(
        f"<option value='{escape(column, quote=True)}'{(' selected' if column == period_selected else '')}>{escape(column)}</option>"
        for column in columns
    )
    detected_text = ", ".join(
        f"{_metric_label(metric)} ← {column}" for metric, column in detected.items()
    ) or "No standard metrics were auto-detected. Choose the source columns manually below."
    default_kind = template.get("kind", "live") if template else "live"
    default_period = template.get("default_period_label", "Imported analytics") if template else "Imported analytics"
    duplicate_notice = (
        "<section class='card'><b>Duplicate prevented</b><p class='muted'>You uploaded the exact same source file again. "
        "No second staged record was created; this original import was opened instead.</p></section>"
        if request.query_params.get("duplicate") == "1"
        else ""
    )

    if item.get("status") == "staged":
        controls = f"""
<section class='card'>
  <h2>Confirm what enters Creator Progress</h2>
  <p class='muted'>Pulsar has suggested mappings where the source headings are recognisable. Review every field. Blank selections are ignored.</p>
  <form method='post' action='/command-center/progress/import/{escape(import_id, quote=True)}/confirm'>
    <div class='grid'>
      <div><label>Progress type</label><select name='kind' required><option value='live'{" selected" if default_kind == "live" else ""}>TikTok LIVE</option><option value='video'{" selected" if default_kind == "video" else ""}>TikTok video</option></select></div>
      <div><label>Period/date source column</label><select name='period_column'>{period_options}</select></div>
      <div><label>Fallback period label</label><input name='default_period_label' maxlength='160' value='{escape(default_period, quote=True)}'></div>
    </div>
    <h3>Metric mapping</h3><div class='grid'>{mapping_fields}</div>
    <label>Notes added to every imported row</label><textarea name='notes' maxlength='2000' placeholder='Optional context about this export or reporting period'></textarea>
    <label>Save this reviewed mapping for future imports (optional)</label><input name='save_mapping_name' maxlength='120' placeholder='TikTok LIVE monthly export'>
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
        f"<style>{base.CSS}textarea{{width:100%;min-height:100px;border:1px solid var(--line);border-radius:10px;padding:10px;background:#080610;color:#fff;margin:5px 0}}label{{display:block;font-weight:800;margin-top:8px}}.danger{{background:#3a1520;color:#fff}}th{{width:180px}}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Import Preview · Human Confirmation</div>"
        f"<h1>{escape(item['upload_name'])}</h1><p class='muted'>{item['row_count']} rows · {escape(item['source_format'].upper())} · status {escape(item['status'])}</p></div>"
        "<div><a class='btn' href='/command-center/progress/import'>All imports</a> <a class='btn' href='/command-center/progress'>Creator Progress</a></div></div>"
        f"{duplicate_notice}{_provenance_panel(member.user_id, import_id)}"
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
    save_mapping_name: str = Form(""),
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
        item = base.data_imports.get(member.user_id, import_id)
        body = base.ImportConfirm(
            kind=kind,
            mapping=mapping,
            period_column=period_column,
            default_period_label=default_period_label,
            notes=notes,
        )
        if save_mapping_name.strip():
            governance.create_mapping(
                member.user_id,
                ImportMappingTemplateCreate(
                    name=save_mapping_name,
                    source_format=item["source_format"],
                    kind=kind,
                    mapping=mapping,
                    period_column=period_column,
                    default_period_label=default_period_label,
                ),
            )
        base.data_imports.confirm(member.user_id, import_id, body)
    except KeyError as exc:
        raise HTTPException(404, "Creator data import not found") from exc
    except FileExistsError as exc:
        raise HTTPException(409, "A saved mapping with that name already exists for this file type") from exc
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


# Browser/governance routes are registered first so richer governed surfaces replace the
# foundation browser versions. Extend with the foundation's remaining JSON APIs afterwards.
router.include_router(governance_router)
router.routes.extend(list(base.router.routes))


__all__ = ["router"]
