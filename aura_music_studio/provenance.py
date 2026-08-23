from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .branding import PRODUCT_FULL_NAME
from .project import ProjectWorkspace


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _output_records(workspace: ProjectWorkspace, exports: dict) -> list[dict]:
    records: list[dict] = []
    for role, raw in sorted(exports.items()):
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if not path.is_absolute():
            candidate = workspace.root / path
            if candidate.exists():
                path = candidate
        if not path.exists() or not path.is_file():
            continue
        records.append(
            {
                "role": role,
                "filename": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return records


def _input_records(workspace: ProjectWorkspace) -> list[dict]:
    assets = _safe_json(workspace.root / "assets.json", [])
    records: list[dict] = []
    for item in assets if isinstance(assets, list) else []:
        records.append(
            {
                "asset_id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "sha256": item.get("sha256"),
                "rights_record_id": item.get("rights_record_id"),
                "tags": item.get("tags") or [],
            }
        )
    return records


def _rights_records(workspace: ProjectWorkspace) -> list[dict]:
    rights_root = workspace.root / ".aura_rights"
    if not rights_root.exists():
        return []
    result = []
    for path in sorted(rights_root.glob("*.json")):
        value = _safe_json(path, None)
        if isinstance(value, dict):
            result.append(value)
    return result


def build_provenance(
    workspace: ProjectWorkspace,
    *,
    manifest: dict,
    renderer: str,
    renderer_metadata: dict,
    audio_origin: str,
    quality_control: dict,
    exports: dict,
) -> dict:
    record = {
        "schema": "esp.live-sound-studio.provenance.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "studio": PRODUCT_FULL_NAME,
        "ai_producer": "Aura",
        "project": {
            "project_name": manifest.get("project_name"),
            "title": manifest.get("title"),
            "mode": manifest.get("mode"),
            "rights_confirmed": manifest.get("rights_confirmed"),
        },
        "generation": {
            "renderer": renderer,
            "renderer_metadata": renderer_metadata,
            "audio_origin": audio_origin,
            "real_audio_final_required": True,
            "symbolic_guide_as_final": False,
        },
        "quality_control": quality_control,
        "inputs": _input_records(workspace),
        "rights_records": _rights_records(workspace),
        "outputs": _output_records(workspace, exports),
        "ownership_notice": (
            "Elevate Souls Productions / The Live Sound Studio does not claim ownership of the member's "
            "original inputs or eligible generated outputs. Applicable law, underlying model licences and "
            "third-party/source-material rights still apply."
        ),
    }

    secret = os.getenv("LSS_PROVENANCE_SECRET") or ""
    unsigned = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    record["integrity"] = {
        "canonical_sha256": hashlib.sha256(unsigned).hexdigest(),
        "hmac_sha256": hmac.new(secret.encode("utf-8"), unsigned, hashlib.sha256).hexdigest() if secret else None,
        "signed": bool(secret),
    }
    return record


def write_provenance(workspace: ProjectWorkspace, record: dict) -> Path:
    output = workspace.output_dir / "ESP_Live_Sound_Studio_Provenance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return output
