from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["Chat 9 Evidence Import Portal"])

CSS = """
:root{color-scheme:dark;--bg:#09050f;--panel:#171020;--panel2:#21142e;--gold:#e7bd63;--text:#fff;--muted:#c8bdd2;--line:#473452;--ok:#78d99c;--bad:#ff93a6}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:radial-gradient(circle at top right,#2b123f 0,#09050f 42%);color:var(--text);margin:0;min-height:100vh}.wrap{max-width:1180px;margin:auto;padding:24px}.top{display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}.card{background:linear-gradient(145deg,var(--panel),#120b19);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.btn,button{display:inline-block;border:0;border-radius:11px;padding:11px 15px;font-weight:800;cursor:pointer;text-decoration:none;background:var(--gold);color:#1c1024}.secondary{background:#352443;color:#fff}.muted{color:var(--muted)}label{display:block;font-weight:750;margin-top:10px}input,select,textarea{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);background:#0e0814;color:#fff;margin-top:6px}textarea{min-height:90px;resize:vertical}.notice{border-left:4px solid var(--gold)}.error{border-left-color:var(--bad)}.ok{border-left-color:var(--ok)}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}a{color:#f3d58d}.hidden{display:none}.checks{display:grid;gap:7px}.checks label{display:flex;gap:9px;align-items:center}.checks input{width:auto;margin:0}pre{white-space:pre-wrap;overflow:auto;max-height:260px;background:#0b0710;border:1px solid var(--line);padding:12px;border-radius:10px}:focus-visible{outline:3px solid #fff;outline-offset:3px}@media(max-width:760px){.grid{grid-template-columns:1fr}.wrap{padding:14px}table{display:block;overflow-x:auto}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main class='wrap'>{body}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/command-center/evidence-import", response_class=HTMLResponse, include_in_schema=False)
def evidence_import_portal(request: Request):
    _member, membership = require_esp_hub_member(request)
    role = escape(str(membership.get("roles") or membership.get("status") or "member"))
    body = """
    <div class='top'><div><div class='muted'>Creator Analytics &amp; Evidence</div><h1>Evidence Import Center</h1>
    <p class='muted'>Preview first. Nothing extracted from a file becomes metric truth until you explicitly select and commit it.</p></div>
    <a class='btn secondary' href='/command-center/level-up'>Back to Level Up</a></div>
    <section class='card notice'><b>Operating context:</b> __ROLE__. TikTok LIVE Backstage is not represented as directly connected here. CSV/XLSX values can be structurally mapped; PDF/image evidence remains human-review-first.</section>

    <section class='card'>
      <h2>1. Preview evidence</h2>
      <form id='previewForm'>
        <div class='grid'>
          <label>Evidence type
            <select name='source_type' id='sourceType' required>
              <option value='csv'>CSV</option><option value='xlsx'>XLSX</option><option value='pdf'>PDF</option><option value='screenshot'>Screenshot / image</option>
            </select>
          </label>
          <label>Provider / source label<input name='provider' maxlength='80' placeholder='TikTok LIVE Studio'></label>
          <label>Creator user ID (leave blank for your own Creator record)<input name='creator_user_id' maxlength='128'></label>
          <label>Immutable evidence asset reference<input name='raw_evidence_ref' required maxlength='512' placeholder='asset:evidence:...'></label>
          <label>Saved mapping<select name='mapping_template_id' id='mappingSelect'><option value=''>Auto-detect exact Metric / Value columns</option></select></label>
          <label>Evidence file<input type='file' name='file' id='fileInput' required accept='.csv,.xlsx,.pdf,image/png,image/jpeg,image/webp'></label>
        </div>
        <button type='submit'>Create review preview</button>
      </form>
      <p class='muted'>The raw evidence reference must point to the canonical asset/evidence store. This page reads the upload for parsing and duplicate hashing; it does not invent a local file path.</p>
    </section>

    <section class='card'>
      <h2>Saved column mapping</h2>
      <p class='muted'>Use this when provider exports do not use literal Metric / Value / Unit headings.</p>
      <form id='mappingForm'>
        <div class='grid'>
          <label>Template name<input name='name' required maxlength='120'></label>
          <label>Source type<select name='source_type' required><option value='csv'>CSV</option><option value='xlsx'>XLSX</option></select></label>
          <label>Metric-name column<input name='metric_column' required maxlength='160' placeholder='Label'></label>
          <label>Value column<input name='value_column' required maxlength='160' placeholder='Result'></label>
          <label>Unit column (optional)<input name='unit_column' maxlength='160' placeholder='Unit'></label>
          <label>Default unit (optional)<input name='default_unit' maxlength='40' placeholder='hours'></label>
        </div>
        <button type='submit'>Save mapping</button>
      </form>
    </section>

    <section id='status' class='card hidden' role='status' aria-live='polite'></section>

    <section id='previewCard' class='card hidden' aria-labelledby='previewHeading'>
      <h2 id='previewHeading'>2. Review preview</h2>
      <div id='previewMeta' class='muted'></div>
      <div id='warnings'></div>
      <div id='metricArea'></div>
      <details><summary>Raw preview detail</summary><pre id='rawPreview'></pre></details>
      <hr>
      <h3>3. Commit reviewed evidence</h3>
      <div class='grid'>
        <label>Reporting period start<input id='periodStart' maxlength='40' placeholder='2026-08-01'></label>
        <label>Reporting period end<input id='periodEnd' maxlength='40' placeholder='2026-08-31'></label>
        <label>Captured at<input id='capturedAt' maxlength='80' placeholder='2026-08-31T23:00:00+00:00'></label>
      </div>
      <label>Import notes<textarea id='importNotes' maxlength='2000' placeholder='Human reviewed before commit.'></textarea></label>
      <button id='commitButton' type='button'>Commit selected metrics / evidence</button>
    </section>

    <script>
    const api='/command-center/api/workflows/evidence';
    let currentPreview=null;
    const $=id=>document.getElementById(id);
    function showStatus(message, ok=true){
      const box=$('status'); box.classList.remove('hidden','ok','error'); box.classList.add(ok?'ok':'error'); box.textContent=message;
    }
    async function jsonRequest(url, options={}){
      const response=await fetch(url, options);
      let data={}; try{data=await response.json();}catch(_e){}
      if(!response.ok){
        const detail=data.detail;
        throw new Error(typeof detail==='string'?detail:(detail?.message||detail?.code||`Request failed (${response.status})`));
      }
      return data;
    }
    async function loadMappings(){
      try{
        const data=await jsonRequest(api+'/mappings');
        const select=$('mappingSelect');
        select.innerHTML="<option value=''>Auto-detect exact Metric / Value columns</option>";
        for(const item of data.mappings||[]){
          const opt=document.createElement('option'); opt.value=item.id; opt.dataset.source=item.source_type;
          opt.textContent=`${item.name} (${item.source_type}: ${item.metric_column} → ${item.value_column})`;
          select.appendChild(opt);
        }
      }catch(e){showStatus(e.message,false);}
    }
    $('sourceType').addEventListener('change',()=>{
      const type=$('sourceType').value;
      for(const opt of $('mappingSelect').options) opt.hidden=!!opt.dataset.source && opt.dataset.source!==type;
      if($('mappingSelect').selectedOptions[0]?.hidden)$('mappingSelect').value='';
    });
    $('mappingForm').addEventListener('submit',async event=>{
      event.preventDefault();
      const form=new FormData(event.currentTarget); const body=Object.fromEntries(form.entries());
      try{
        await jsonRequest(api+'/mappings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        showStatus('Mapping template saved.',true); event.currentTarget.reset(); await loadMappings();
      }catch(e){showStatus(e.message,false);}
    });
    $('previewForm').addEventListener('submit',async event=>{
      event.preventDefault(); $('previewCard').classList.add('hidden');
      const form=new FormData(event.currentTarget);
      try{
        const data=await jsonRequest(api+'/import-preview',{method:'POST',body:form});
        currentPreview=data.preview;
        const parsed=currentPreview.preview||{};
        $('previewMeta').textContent=`${parsed.original_filename||''} · ${parsed.file_size||0} bytes · SHA-256 ${parsed.source_sha256||''}`+
          (currentPreview.duplicate_source?` · DUPLICATE of batch ${currentPreview.existing_batch_id||''}`:'');
        const warnings=$('warnings'); warnings.innerHTML='';
        for(const warning of parsed.warnings||[]){const p=document.createElement('p');p.className='muted';p.textContent=warning;warnings.appendChild(p);}
        const area=$('metricArea'); area.innerHTML='';
        const candidates=parsed.candidate_metrics||[];
        if(candidates.length){
          const heading=document.createElement('h3');heading.textContent='Candidate metrics — select only values you have reviewed';area.appendChild(heading);
          const checks=document.createElement('div');checks.className='checks';
          for(const metric of candidates){
            const label=document.createElement('label'); const cb=document.createElement('input');cb.type='checkbox';cb.value=metric.name;cb.checked=false;
            const text=document.createElement('span');text.textContent=`${metric.name} = ${metric.value===null?'missing':metric.value} ${metric.unit||''}`;
            label.append(cb,text);checks.appendChild(label);
          }
          area.appendChild(checks);
        } else {
          const p=document.createElement('p');p.className='muted';p.textContent='No structured metrics were generated automatically. You may still commit the raw evidence batch with zero metrics and add/correct structured metrics through the reviewed evidence workflow.';area.appendChild(p);
        }
        $('rawPreview').textContent=JSON.stringify(parsed,null,2);
        $('previewCard').classList.remove('hidden');
        showStatus(currentPreview.duplicate_source?'Preview created, but this exact source is already committed for the Creator.':'Preview created. Review before committing.',!currentPreview.duplicate_source);
      }catch(e){currentPreview=null;showStatus(e.message,false);}
    });
    $('commitButton').addEventListener('click',async()=>{
      if(!currentPreview) return showStatus('Create a preview first.',false);
      const selected=[...$('metricArea').querySelectorAll("input[type='checkbox']:checked")].map(x=>x.value);
      const body={preview_id:currentPreview.id,selected_metric_names:selected,period_start:$('periodStart').value||null,period_end:$('periodEnd').value||null,captured_at:$('capturedAt').value||null,notes:$('importNotes').value};
      try{
        const data=await jsonRequest(api+'/import-commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        showStatus(`Evidence committed as batch ${data.batch.id}. Imported snapshots remain labelled non-realtime.`,true);
      }catch(e){showStatus(e.message,false);}
    });
    loadMappings(); $('sourceType').dispatchEvent(new Event('change'));
    </script>
    """.replace("__ROLE__", role)
    return _page("Evidence Import Center", body)


__all__ = ["router"]
