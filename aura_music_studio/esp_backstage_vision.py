from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_multimodal import AuraVisionService
from .esp_backstage_evidence import BackstageEvidenceStore, _canonical_metrics, backstage_evidence
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Backstage Screenshot Vision"])

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_METRIC_KEYS = (
    "views", "unique_viewers", "duration_minutes", "avg_watch_seconds", "peak_viewers",
    "new_followers", "comments", "shares", "likes", "diamonds", "gifters",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_agent(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership, role == "owner"


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Aura vision did not return a JSON object")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("Aura vision returned invalid metric JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Aura vision metric response must be an object")
    return payload


class VisionConfirmation(BaseModel):
    confirm: bool = True
    metrics: dict = Field(default_factory=dict)
    reviewer_note: str = Field(default="", max_length=2000)


class BackstageVisionStore:
    """Vision-assisted extraction with mandatory human confirmation before progress use."""

    def __init__(self, evidence_store: BackstageEvidenceStore | None = None, vision_factory=None):
        self.evidence = evidence_store or backstage_evidence
        self.db_path = self.evidence.db_path
        self.vision_factory = vision_factory or AuraVisionService
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_backstage_vision_runs (
                    id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL,
                    requested_by_user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    proposed_metrics_json TEXT NOT NULL DEFAULT '{}',
                    raw_analysis TEXT NOT NULL DEFAULT '',
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    FOREIGN KEY(evidence_id) REFERENCES esp_backstage_evidence(id) ON DELETE CASCADE,
                    FOREIGN KEY(requested_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_backstage_vision_evidence
                    ON esp_backstage_vision_runs(evidence_id,created_at DESC);
                """
            )

    def _source_row(self, evidence_id: str, actor_user_id: str, *, owner: bool):
        public = self.evidence.get(evidence_id, actor_user_id=actor_user_id, owner=owner)
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_backstage_evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError(evidence_id)
        return public, row

    @staticmethod
    def _safe_path(row) -> Path:
        stored = Path(str(row["upload_path"] or "")).resolve()
        suffix = stored.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            raise ValueError("Aura screenshot extraction supports PNG, JPG/JPEG and WEBP evidence")
        creator_root = (Path(os.getenv("ESP_PROGRESS_ROOT", "data/esp_progress")).resolve() / row["creator_user_id"]).resolve()
        if creator_root not in stored.parents:
            raise ValueError("Evidence path is outside the creator's private progress storage")
        if not stored.is_file():
            raise FileNotFoundError("Backstage evidence image is missing")
        return stored

    def analyze(self, evidence_id: str, actor_user_id: str, *, owner: bool = False) -> dict:
        public, row = self._source_row(evidence_id, actor_user_id, owner=owner)
        path = self._safe_path(row)
        vision = self.vision_factory()
        if not vision.configured:
            raise RuntimeError("Aura vision is not configured for Backstage screenshot analysis")
        instruction = (
            "This is an authorised TikTok LIVE analytics / Manage Creator screenshot supplied for ESP mentoring. "
            "Extract only metric values visibly supported by the screenshot. Do not infer hidden values and do not identify people. "
            "Return one strict JSON object only, shaped as: "
            '{"metrics":{"views":null,"unique_viewers":null,"duration_minutes":null,"avg_watch_seconds":null,'
            '"peak_viewers":null,"new_followers":null,"comments":null,"shares":null,"likes":null,"diamonds":null,'
            '"gifters":null},"confidence":0.0,"notes":"brief visible-data caveat"}. '
            "Use numbers without commas or percent signs. Leave unsupported fields null. Confidence must be 0 to 1."
        )
        raw = vision.analyze_images([path], instruction)
        payload = _extract_json(raw)
        metric_payload = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
        metrics = _canonical_metrics(metric_payload)
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        status = "pending_confirmation" if metrics else "no_supported_metrics"
        run_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_backstage_vision_runs
                   (id,evidence_id,requested_by_user_id,status,model,confidence,proposed_metrics_json,raw_analysis,
                    reviewer_note,created_at,confirmed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    run_id, evidence_id, actor_user_id, status, str(getattr(vision, "model", "") or "")[:160],
                    confidence, json.dumps(metrics, sort_keys=True), raw[:12000], "", _now(),
                ),
            )
            con.execute(
                "UPDATE esp_backstage_evidence SET extraction_status=? WHERE id=?",
                ("vision_pending_confirmation" if metrics else "vision_no_supported_metrics", evidence_id),
            )
        return {
            "run_id": run_id,
            "evidence_id": evidence_id,
            "creator_user_id": public["creator_user_id"],
            "proposed_metrics": metrics,
            "confidence": confidence,
            "status": status,
            "human_confirmation_required": True,
            "progress_updated": False,
            "direct_backstage_access": False,
        }

    def run(self, run_id: str, actor_user_id: str, *, owner: bool = False) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_backstage_vision_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        evidence = self.evidence.get(row["evidence_id"], actor_user_id=actor_user_id, owner=owner)
        item = dict(row)
        item["proposed_metrics"] = json.loads(item.pop("proposed_metrics_json") or "{}")
        item.pop("raw_analysis", None)
        item["creator_user_id"] = evidence["creator_user_id"]
        item["human_confirmation_required"] = item["status"] == "pending_confirmation"
        return item

    def confirm(
        self,
        run_id: str,
        actor_user_id: str,
        *,
        owner: bool = False,
        confirm: bool,
        metrics: dict | None = None,
        reviewer_note: str = "",
    ) -> dict:
        run = self.run(run_id, actor_user_id, owner=owner)
        if run["status"] != "pending_confirmation":
            raise ValueError("This vision extraction is no longer awaiting confirmation")
        evidence_id = run["evidence_id"]
        _public, source = self._source_row(evidence_id, actor_user_id, owner=owner)
        note = " ".join((reviewer_note or "").split())[:2000]
        if not confirm:
            with self._connect() as con:
                con.execute(
                    "UPDATE esp_backstage_vision_runs SET status='rejected',reviewer_note=?,confirmed_at=? WHERE id=?",
                    (note, _now(), run_id),
                )
                con.execute(
                    "UPDATE esp_backstage_evidence SET extraction_status='visual_review_required' WHERE id=?",
                    (evidence_id,),
                )
            return {
                "run_id": run_id, "evidence_id": evidence_id, "status": "rejected",
                "progress_updated": False, "direct_backstage_access": False,
            }
        reviewed = _canonical_metrics(metrics if metrics is not None else run["proposed_metrics"])
        if not reviewed:
            raise ValueError("Confirm at least one visible metric or reject the extraction")
        guidance = self.evidence.progress.guidance(source["creator_user_id"], "live", reviewed)
        progress_id = source["progress_submission_id"]
        if not progress_id:
            progress_row = self.evidence.progress.add(
                source["creator_user_id"],
                kind="live",
                period_label=source["period_label"] or "Vision-confirmed TikTok LIVE evidence",
                metrics=reviewed,
                notes=(
                    "Agent/owner confirmed metrics extracted from an authorised screenshot using Aura vision. "
                    "This is not a direct TikTok LIVE Backstage connection."
                ),
                upload_name=source["upload_name"],
                upload_path=source["upload_path"],
                upload_content_type=source["upload_content_type"],
            )
            progress_id = progress_row.get("id")
        with self._connect() as con:
            con.execute(
                """UPDATE esp_backstage_evidence
                   SET extraction_status='vision_confirmed',metrics_json=?,guidance_json=?,progress_submission_id=?
                   WHERE id=?""",
                (json.dumps(reviewed, sort_keys=True), json.dumps(guidance), progress_id, evidence_id),
            )
            con.execute(
                """UPDATE esp_backstage_vision_runs
                   SET status='confirmed',proposed_metrics_json=?,reviewer_note=?,confirmed_at=? WHERE id=?""",
                (json.dumps(reviewed, sort_keys=True), note, _now(), run_id),
            )
        return {
            "run_id": run_id,
            "evidence": self.evidence.get(evidence_id, actor_user_id=actor_user_id, owner=owner),
            "status": "confirmed",
            "progress_updated": True,
            "direct_backstage_access": False,
        }


vision_runs = BackstageVisionStore()


@router.get("/command-center/api/agent/backstage-vision/status")
def backstage_vision_status(request: Request):
    _member, _membership, _owner = _require_agent(request)
    diagnostics = AuraVisionService().diagnostics()
    return {
        "vision": diagnostics,
        "supported_evidence": sorted(_IMAGE_SUFFIXES),
        "metric_keys": list(_METRIC_KEYS),
        "human_confirmation_required": True,
        "direct_backstage_access": False,
    }


@router.post("/command-center/api/agent/backstage-evidence/{evidence_id}/vision")
def analyze_backstage_screenshot(evidence_id: str, request: Request):
    member, _membership, owner = _require_agent(request)
    try:
        return vision_runs.analyze(evidence_id, member.user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Backstage evidence not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/command-center/api/agent/backstage-vision/{run_id}/confirm")
def confirm_backstage_vision(run_id: str, body: VisionConfirmation, request: Request):
    member, _membership, owner = _require_agent(request)
    try:
        return vision_runs.confirm(
            run_id, member.user_id, owner=owner, confirm=body.confirm,
            metrics=body.metrics if body.metrics else None, reviewer_note=body.reviewer_note,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Vision extraction not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


__all__ = ["router", "BackstageVisionStore", "vision_runs", "_extract_json"]
