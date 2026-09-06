from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .chord_detection import ALLOWED_AUDIO_SUFFIXES, _member, _project

router = APIRouter(tags=["Chord Intelligence"])


def discover_project_audio_refs(project: Path, *, limit: int = 250) -> list[str]:
    """Return a bounded set of project-local audio refs suitable for detection."""
    found: set[str] = set()
    roots = [project / name for name in ("input", "uploads", "recordings", "assets", "output")]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if len(found) >= limit:
                break
            if not path.is_file() or path.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
                continue
            try:
                ref = str(path.resolve().relative_to(project.resolve())).replace("\\", "/")
            except ValueError:
                continue
            found.add(ref)
        if len(found) >= limit:
            break
    return sorted(found)[:limit]


@router.get("/song-editor/{project_name}/chord-detection", response_class=HTMLResponse, include_in_schema=False)
def chord_detection_portal(project_name: str, request: Request):
    _member(request)
    project = _project(project_name)
    encoded = quote(project_name, safe="")
    refs = discover_project_audio_refs(project)
    options = "".join(
        f"<option value='{html.escape(ref, quote=True)}'>{html.escape(ref)}</option>" for ref in refs
    )
    if not options:
        options = "<option value=''>No project audio found — import or upload audio first</option>"
    disabled = " disabled" if not refs else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Detect Chords · {html.escape(project_name)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#11131a;color:#f5f5f7}}main{{max-width:1080px;margin:auto;padding:24px}}a{{color:#cbb7ff}}.card{{background:#191c25;padding:16px;border-radius:14px;margin:16px 0}}label{{display:grid;gap:6px}}input,select,button{{font:inherit;padding:9px;border-radius:8px;border:1px solid #555}}button{{cursor:pointer}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #343746;text-align:left}}#status{{min-height:1.5em}}.good{{border-color:#7edb9b}}.warn{{color:#ffd27a}}
</style></head><body><main>
<p><a href='/song-editor/{encoded}'>← Song Editor</a> · <a href='/song-editor/{encoded}/chords'>Chord Studio</a></p>
<h1>Detect Chords from Audio</h1>
<p>This local analyser currently recognises <strong>major and minor triads</strong>. It creates a preview first and does not change Song DNA until you explicitly commit that exact preview.</p>
<div class='card'><form id='detectForm'>
<div class='grid'>
<label>Project audio<select name='source_ref' required{disabled}>{options}</select></label>
<label>Minimum segment (seconds)<input name='minimum_segment_seconds' type='number' min='0.2' max='4' step='0.05' value='0.45'></label>
<label>Confidence threshold<input name='confidence_threshold' type='number' min='0.05' max='0.95' step='0.01' value='0.45'></label>
<label>Key hint (optional)<input name='key_hint' maxlength='24' placeholder='C major'></label>
</div><p><button type='submit'{disabled}>Analyse audio</button></p></form>
<p id='status' role='status' aria-live='polite'></p></div>
<div class='card' id='preview' hidden><h2>Detection preview</h2><p id='truth' class='warn'></p><div style='overflow:auto'><table><thead><tr><th>Time</th><th>Chord</th><th>Confidence</th><th>Roman</th></tr></thead><tbody id='rows'></tbody></table></div>
<div class='grid' style='margin-top:14px'><label>Commit key (optional)<input id='commitKey' maxlength='24' placeholder='Use preview key hint'></label><div><button id='commitButton' type='button' class='good'>Commit exact preview to Song DNA</button></div></div></div>
<script>
const encoded={encoded!r};
const base='/projects/'+encoded+'/song-dna/chords';
const status=document.getElementById('status'); const preview=document.getElementById('preview');
const rows=document.getElementById('rows'); const truth=document.getElementById('truth');
let candidate=null;
function esc(value){{return String(value??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));}}
document.getElementById('detectForm').addEventListener('submit',async e=>{{e.preventDefault(); candidate=null; preview.hidden=true; status.textContent='Analysing project audio locally…'; const f=new FormData(e.currentTarget); const body={{source_ref:f.get('source_ref'),minimum_segment_seconds:Number(f.get('minimum_segment_seconds')),confidence_threshold:Number(f.get('confidence_threshold')),key_hint:String(f.get('key_hint')||'').trim()||null}}; try{{const r=await fetch(base+'/detect-preview',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}); const data=await r.json().catch(()=>({{}})); if(!r.ok) throw new Error(data.detail||'Detection failed'); candidate=data; rows.innerHTML=data.events.map((event,index)=>{{const analysis=(data.analysis||[])[index]||{{}}; return `<tr><td>${{Number(event.start_seconds).toFixed(2)}}s–${{Number(event.end_seconds).toFixed(2)}}s</td><td>${{esc(event.symbol)}}</td><td>${{Math.round(Number(event.metadata?.detection_confidence||0)*100)}}%</td><td>${{esc(analysis.roman_numeral||'—')}}</td></tr>`;}}).join(''); truth.textContent=`Preview only · ${{data.events.length}} segments · ${{data.quality_scope}} · provider invoked: ${{data.provider_invoked?'yes':'no'}} · Song DNA unchanged.`; status.textContent='Preview ready. Review it before committing.'; preview.hidden=false;}}catch(err){{status.textContent=err.message;}}}});
document.getElementById('commitButton').addEventListener('click',async()=>{{if(!candidate)return; status.textContent='Committing the exact reviewed candidate…'; const key=document.getElementById('commitKey').value.trim()||null; try{{const r=await fetch(base+'/detect-commit',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{candidate_id:candidate.candidate_id,candidate_sha256:candidate.candidate_sha256,key}})}}); const data=await r.json().catch(()=>({{}})); if(!r.ok)throw new Error(data.detail||'Commit failed'); status.textContent=`Committed to Song DNA version ${{data.song_dna_version}} with revision ${{data.revision_id}}. Audio rendered: ${{data.audio_rendered?'yes':'no'}}; MIDI rewritten: ${{data.midi_rewritten?'yes':'no'}}.`; document.getElementById('commitButton').disabled=true;}}catch(err){{status.textContent=err.message;}}}});
</script></main></body></html>"""
    )


__all__ = ["chord_detection_portal", "discover_project_audio_refs", "router"]
