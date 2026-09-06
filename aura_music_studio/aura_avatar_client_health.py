from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .aura_avatar_speech_sync import router as avatar_speech_sync_router

router = APIRouter(tags=["Aura Avatar"])
router.include_router(avatar_speech_sync_router)

ClientClass = Literal["mobile", "tablet", "desktop", "unknown"]
RendererErrorCode = Literal[
    "none",
    "webgl_unavailable",
    "renderer_module_failed",
    "model_load_failed",
    "unknown",
]


class AvatarClientHealthReport(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    client_class: ClientClass = "unknown"
    page_hidden: bool = False
    webgl: bool = False
    webgl2: bool = False
    renderer_attempted: bool = False
    renderer_loaded: bool = False
    model_loaded: bool = False
    layered_performance_supported: bool = False
    model_load_ms: float | None = Field(default=None, ge=0.0, le=120000.0)
    frame_rate_fps: float | None = Field(default=None, ge=0.0, le=240.0)
    frame_samples: int = Field(default=0, ge=0, le=2000)
    renderer_error_code: RendererErrorCode = "none"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _health_db_path() -> Path:
    configured = (os.getenv("AURA_AVATAR_HEALTH_DB") or "").strip()
    path = Path(configured).expanduser() if configured else Path("data/aura/avatar-health.db")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _derived_state(report: AvatarClientHealthReport) -> str:
    if not report.webgl:
        return "webgl_unavailable"
    if report.renderer_error_code != "none":
        return "renderer_error"
    if report.renderer_attempted and not report.renderer_loaded:
        return "renderer_load_incomplete"
    if report.renderer_loaded and not report.model_loaded:
        return "model_load_incomplete"
    if report.page_hidden:
        return "sample_backgrounded"
    if report.frame_rate_fps is not None and report.frame_samples >= 20 and report.frame_rate_fps < 24.0:
        return "frame_cadence_degraded"
    if report.model_load_ms is not None and report.model_load_ms > 15000.0:
        return "model_load_slow"
    if report.renderer_loaded and report.model_loaded:
        return "healthy_3d_session"
    return "runtime_capable_no_3d_attempt"


class AvatarClientHealthStore:
    """Durable, privacy-bounded evidence for Aura browser/device runtime health.

    No IP address, user-agent string, viewport dimensions, CPU/memory details, browser
    fingerprint, model identifier, hostname or raw error text is collected. Reports are scoped
    to the signed-in member and pruned to a small rolling window because this evidence is
    operational diagnostics, not identity telemetry.
    """

    def __init__(self, db_path: str | Path | None = None, *, keep_per_user: int = 24):
        self.db_path = Path(db_path).resolve() if db_path is not None else _health_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.keep_per_user = max(4, min(int(keep_per_user), 100))
        self._lock = RLock()
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_schema(self) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS aura_avatar_client_health (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    client_class TEXT NOT NULL,
                    derived_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_aura_avatar_health_user_time "
                "ON aura_avatar_client_health(user_id, captured_at DESC)"
            )

    @staticmethod
    def _public_row(row: sqlite3.Row) -> dict:
        payload = json.loads(row["payload_json"])
        return {
            "id": row["id"],
            "captured_at": row["captured_at"],
            "client_class": row["client_class"],
            "derived_state": row["derived_state"],
            "report": payload,
        }

    def record(self, user_id: str, report: AvatarClientHealthReport) -> dict:
        row_id = uuid4().hex
        captured_at = _utcnow()
        derived_state = _derived_state(report)
        payload = report.model_dump(mode="json")
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO aura_avatar_client_health "
                "(id, user_id, captured_at, client_class, derived_state, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    str(user_id),
                    captured_at,
                    report.client_class,
                    derived_state,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                ),
            )
            con.execute(
                "DELETE FROM aura_avatar_client_health WHERE user_id=? AND id NOT IN ("
                "SELECT id FROM aura_avatar_client_health WHERE user_id=? "
                "ORDER BY captured_at DESC, id DESC LIMIT ?)",
                (str(user_id), str(user_id), self.keep_per_user),
            )
        return {
            "id": row_id,
            "captured_at": captured_at,
            "client_class": report.client_class,
            "derived_state": derived_state,
            "report": payload,
        }

    def recent(self, user_id: str, *, limit: int = 24) -> list[dict]:
        bounded = max(1, min(int(limit), self.keep_per_user))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, captured_at, client_class, derived_state, payload_json "
                "FROM aura_avatar_client_health WHERE user_id=? "
                "ORDER BY captured_at DESC, id DESC LIMIT ?",
                (str(user_id), bounded),
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def summary(self, user_id: str) -> dict:
        rows = self.recent(user_id, limit=self.keep_per_user)
        healthy = [row for row in rows if row["derived_state"] == "healthy_3d_session"]
        classes = sorted({row["client_class"] for row in rows})
        healthy_classes = sorted({row["client_class"] for row in healthy})
        latest = rows[0] if rows else None
        return {
            "latest": latest,
            "samples": rows,
            "sample_count": len(rows),
            "client_classes_observed": classes,
            "healthy_client_classes_observed": healthy_classes,
            "has_healthy_3d_session": bool(healthy),
            "evidence_scope": "signed_in_member_browser_runtime_only",
            "privacy_contract": {
                "ip_address_collected": False,
                "user_agent_collected": False,
                "viewport_dimensions_collected": False,
                "cpu_or_memory_details_collected": False,
                "raw_renderer_error_collected": False,
                "device_fingerprint_collected": False,
                "coarse_client_class_only": True,
                "rolling_sample_limit_per_member": self.keep_per_user,
            },
            "readiness_authority": False,
            "production_3d_ready_can_be_promoted_by_client_health": False,
            "operator_validation_still_required": True,
        }


store = AvatarClientHealthStore()


@router.post("/aura-intelligence/api/avatar/client-health")
def record_avatar_client_health(body: AvatarClientHealthReport, request: Request):
    member = _member(request)
    row = store.record(member.user_id, body)
    return {
        **row,
        "readiness_authority": False,
        "production_3d_ready_changed": False,
        "operator_validation_still_required": True,
    }


@router.get("/aura-intelligence/api/avatar/client-health")
def get_avatar_client_health(request: Request, limit: int = 24):
    member = _member(request)
    result = store.summary(member.user_id)
    if limit < len(result["samples"]):
        result["samples"] = result["samples"][: max(1, min(int(limit), store.keep_per_user))]
        result["sample_count"] = len(result["samples"])
    return result


CLIENT_HEALTH_SCRIPT = r"""
(()=>{
  const HEALTH='/aura-intelligence/api/avatar/client-health';
  const STATUS='/aura-intelligence/api/avatar/status';
  const SPEECH_SYNC='/aura-intelligence/avatar-speech-sync.js';
  const started=performance.now();
  let rendererAttempted=false,rendererLoaded=false,modelLoaded=false,layered=false,modelLoadMs=null,errorCode='none';
  let frames=0,frameStart=null,frameEnd=null,submitted=false;

  function loadSpeechSync(){
    if(document.querySelector("script[data-aura-speech-sync='1']"))return;
    const script=document.createElement('script');script.src=SPEECH_SYNC;script.async=false;script.dataset.auraSpeechSync='1';document.head.append(script);
  }
  function classifyClient(){
    const mobileHint=!!navigator.userAgentData?.mobile;
    const width=Math.max(1,window.innerWidth||document.documentElement.clientWidth||1);
    if(mobileHint||width<=760)return'mobile';
    if(width<=1180)return'tablet';
    return'desktop';
  }
  function webglState(){
    try{const canvas=document.createElement('canvas');const webgl2=!!canvas.getContext('webgl2',{failIfMajorPerformanceCaveat:false});const webgl=webgl2||!!canvas.getContext('webgl',{failIfMajorPerformanceCaveat:false});return{webgl,webgl2}}catch(_){return{webgl:false,webgl2:false}}
  }
  function frameTick(now){
    if(frameStart==null)frameStart=now;frameEnd=now;frames+=1;
    if(now-frameStart<2600)requestAnimationFrame(frameTick);else submit();
  }
  function frameRate(){
    if(frameStart==null||frameEnd==null||frameEnd<=frameStart||frames<2)return null;
    return Math.min(240,Math.max(0,(frames-1)*1000/(frameEnd-frameStart)));
  }
  function noteWarning(event){
    const detail=event?.detail||{};
    if(detail.state!=='warning')return;
    const reason=String(detail.reason||'');
    if(reason==='3d_model_load_failed')errorCode='model_load_failed';
    else if(reason.toLowerCase().includes('renderer module'))errorCode='renderer_module_failed';
    else if(rendererAttempted&&errorCode==='none')errorCode='unknown';
  }
  async function submit(){
    if(submitted)return;submitted=true;
    const gl=webglState();
    if(!gl.webgl&&errorCode==='none')errorCode='webgl_unavailable';
    const body={
      schema_version:1,
      client_class:classifyClient(),
      page_hidden:!!document.hidden,
      webgl:gl.webgl,
      webgl2:gl.webgl2,
      renderer_attempted:rendererAttempted,
      renderer_loaded:rendererLoaded,
      model_loaded:modelLoaded,
      layered_performance_supported:layered,
      model_load_ms:modelLoadMs,
      frame_rate_fps:frameRate(),
      frame_samples:Math.min(2000,frames),
      renderer_error_code:errorCode,
    };
    try{
      const response=await fetch(HEALTH,{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(response.ok){const result=await response.json();document.dispatchEvent(new CustomEvent('aura:client-health',{detail:result}))}
    }catch(_){/* Client diagnostics must never interrupt Aura interaction. */}
  }

  loadSpeechSync();
  document.addEventListener('aura:state',noteWarning);
  document.addEventListener('aura:3d-ready',event=>{
    rendererAttempted=true;rendererLoaded=true;modelLoaded=true;layered=!!event.detail?.layeredPerformanceSupported;modelLoadMs=Math.max(0,performance.now()-started);
  });
  fetch(STATUS,{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(status=>{
    rendererAttempted=!!(status?.model_valid&&status?.renderer_configured);
  }).catch(()=>{}).finally(()=>requestAnimationFrame(frameTick));
  setTimeout(submit,6500);
  window.addEventListener('beforeunload',()=>{if(!submitted)submit()});
})();
"""


@router.get("/aura-intelligence/avatar-client-health.js", include_in_schema=False)
def avatar_client_health_script():
    return Response(
        content=CLIENT_HEALTH_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


__all__ = [
    "router",
    "store",
    "AvatarClientHealthStore",
    "AvatarClientHealthReport",
    "CLIENT_HEALTH_SCRIPT",
    "_derived_state",
]
