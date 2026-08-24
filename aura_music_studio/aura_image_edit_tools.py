from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .aura_companion import AuraCompanionError
from .aura_system_companion import AuraSystemCompanionService
from .image_editing import ImageEditError, ImageEditRequest, ImageEditingService
from .image_jobs import ImageJobStore
from .plans import ADVANCED_IMAGE_GENERATION, IMAGE_GENERATION, IMAGE_HIGH_QUALITY, IMAGE_PROVIDER_CONTROL

_ORIGINAL_DEFINITIONS = AuraSystemCompanionService._tool_definitions
_ORIGINAL_EXECUTE = AuraSystemCompanionService._execute_tool
_ORIGINAL_PROMPT = AuraSystemCompanionService._system_prompt
_INSTALLED = False

_store = ImageJobStore(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
_editor = ImageEditingService(os.getenv("AURA_IMAGE_OUTPUT_DIR", "outputs/images"))


def _tool_definitions(self, member) -> list[dict[str, Any]]:
    tools = list(_ORIGINAL_DEFINITIONS(self, member))
    if not tools or not member.plan.has(IMAGE_GENERATION):
        return tools
    tools.extend(
        [
            {
                "type": "function",
                "name": "list_my_images",
                "description": (
                    "List the signed-in member's recent generated/edited images so Aura can identify the correct source before editing. "
                    "Use this when the user says things like 'edit my last poster' and no source job id is already in context."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}},
                    "required": ["limit"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "edit_image",
                "description": (
                    "Create a real non-destructive child revision of one of the signed-in member's completed image jobs. "
                    "The original image is never overwritten. Use a precise natural-language edit instruction and preserve unrelated details."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_job_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "quality": {"type": "string", "enum": ["standard", "high", "professional"]},
                        "provider": {"type": "string", "enum": ["auto", "local", "openai"]},
                        "aspect_ratio": {"type": "string", "enum": ["1:1", "4:5", "3:2", "2:3", "16:9", "9:16"]},
                        "preserve_subject": {"type": "boolean"},
                        "edit_strength": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["source_job_id", "prompt", "quality", "provider", "aspect_ratio", "preserve_subject", "edit_strength"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "get_image_edit_lineage",
                "description": "Show the parent/child revision relationship for one of the signed-in member's images.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    return tools


def _system_prompt(self, member, *, project_context: dict | None = None) -> str:
    base = _ORIGINAL_PROMPT(self, member, project_context=project_context)
    return base + (
        " Image creation is revision-based. When a member asks to change an existing poster/image/cover, do not regenerate blindly if a source image exists. "
        "Identify the correct member-owned source with list_my_images when necessary, then use edit_image. Treat every edit as a new child revision; never imply the original was overwritten. "
        "Keep unrelated visual details stable unless the member asks for a broad reinterpretation. If the source is ambiguous, ask which image rather than guessing. "
        "Use get_image_edit_lineage when the member asks about versions, where an edit came from, or wants to compare the revision chain."
    )


def _safe_source(member, source_job_id: str) -> tuple[dict[str, Any], Path]:
    job = _store.get_for_user(member.user_id, source_job_id)
    if not job:
        raise AuraCompanionError("Source image job not found in this member's library")
    if job.get("status") != "completed" or not job.get("output_path"):
        raise AuraCompanionError("Source image is not ready for editing")
    root = _editor.output_root.resolve()
    source = Path(job["output_path"]).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise AuraCompanionError("Source image output is unavailable")
    return job, source


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _execute_tool(self, member, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "list_my_images":
        rows = _store.list_for_user(member.user_id, limit=int(arguments.get("limit") or 10))
        return {
            "images": [
                {
                    "job_id": row["id"],
                    "project_id": row.get("project_id"),
                    "mode": row.get("mode"),
                    "prompt": row.get("prompt"),
                    "status": row.get("status"),
                    "provider": row.get("provider"),
                    "model": row.get("model"),
                    "created_at": row.get("created_at"),
                    "download_url": f"/api/image/jobs/{row['id']}/download" if row.get("status") == "completed" else None,
                }
                for row in rows
            ]
        }

    if name == "get_image_edit_lineage":
        try:
            return _store.lineage_for_user(member.user_id, str(arguments.get("job_id") or ""))
        except KeyError as exc:
            raise AuraCompanionError("Image job not found in this member's library") from exc

    if name == "edit_image":
        if not member.plan.has(IMAGE_GENERATION):
            raise AuraCompanionError("Image editing is not available on this membership")
        quality = str(arguments.get("quality") or "standard")
        provider = str(arguments.get("provider") or "auto")
        preserve_subject = bool(arguments.get("preserve_subject", True))
        strength = float(arguments.get("edit_strength", 0.65))
        if quality != "standard" and not member.plan.has(IMAGE_HIGH_QUALITY):
            raise AuraCompanionError("High-quality image editing requires Pro")
        if provider != "auto" and not member.plan.has(IMAGE_PROVIDER_CONTROL):
            raise AuraCompanionError("Manual image-editor selection requires Pro")
        if ((not preserve_subject) or abs(strength - 0.65) > 1e-9) and not member.plan.has(ADVANCED_IMAGE_GENERATION):
            raise AuraCompanionError("Advanced image edit strength/composition control requires Pro")

        source_job_id = str(arguments.get("source_job_id") or "").strip()
        source_job, source = _safe_source(member, source_job_id)
        source_sha = _sha256(source)
        edit_request = ImageEditRequest(
            source_job_id=source_job_id,
            prompt=str(arguments.get("prompt") or ""),
            quality=quality,
            provider=provider,
            aspect_ratio=str(arguments.get("aspect_ratio") or "1:1"),
            project_id=source_job.get("project_id"),
            preserve_subject=preserve_subject,
            edit_strength=strength,
        )
        try:
            result = _editor.edit(source, edit_request)
        except ImageEditError as exc:
            raise AuraCompanionError(str(exc)) from exc
        provenance_hash = _editor.provenance_hash(result, source_sha256=source_sha)
        _store.save(
            user_id=member.user_id,
            result=result.to_dict(),
            mode="edit",
            prompt=edit_request.prompt,
            project_id=source_job.get("project_id"),
            provenance_hash=provenance_hash,
        )
        lineage = _store.save_edit_lineage(
            user_id=member.user_id,
            parent_job_id=source_job_id,
            child_job_id=result.id,
            edit_prompt=edit_request.prompt,
            source_sha256=source_sha,
        )
        return {
            "job_id": result.id,
            "status": result.status,
            "provider": result.provider,
            "model": result.model,
            "parent_job_id": source_job_id,
            "provenance_hash": provenance_hash,
            "lineage": lineage,
            "non_destructive": True,
            "source_preserved": True,
            "download_url": f"/api/image/jobs/{result.id}/download",
        }

    return _ORIGINAL_EXECUTE(self, member, name, arguments)


def install_aura_image_edit_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    AuraSystemCompanionService._tool_definitions = _tool_definitions
    AuraSystemCompanionService._system_prompt = _system_prompt
    AuraSystemCompanionService._execute_tool = _execute_tool
    _INSTALLED = True


install_aura_image_edit_tools()
