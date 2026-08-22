from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_project
from .arrangement import build_plan
from .audio import finalize_render
from .guide import ensure_score_guide
from .models import ProjectManifest
from .project import ProjectWorkspace
from .renderers import render_with_failover


class AuraPipeline:
    def __init__(self, project_root: str | Path):
        self.workspace = ProjectWorkspace(Path(project_root))
        self.manifest: ProjectManifest = self.workspace.load_manifest()

    def run(self) -> dict:
        started = datetime.now(timezone.utc).isoformat()
        status = {
            "project": self.manifest.project_name,
            "started_at": started,
            "stage": "starting",
            "success": False,
        }
        self._write_status(status)
        try:
            status["stage"] = "analysis"
            self._write_status(status)
            analysis = analyze_project(self.workspace, self.manifest)
            self.workspace.save_json("analysis.json", analysis.model_dump())

            status["stage"] = "arrangement"
            self._write_status(status)
            plan = build_plan(self.manifest, analysis)
            self.workspace.save_json("arrangement.json", plan.model_dump())

            status["stage"] = "guide"
            self._write_status(status)
            guide = ensure_score_guide(self.workspace, self.manifest)
            if guide:
                status["guide"] = str(guide)

            status["stage"] = "neural_render"
            self._write_status(status)
            render = render_with_failover(self.workspace, self.manifest, plan)
            status["renderer"] = render.renderer
            status["neural_master"] = str(render.audio_path)
            self.workspace.save_json("render.json", {
                "renderer": render.renderer,
                "audio_path": str(render.audio_path),
                "metadata": render.metadata,
            })

            status["stage"] = "mastering_and_stems"
            self._write_status(status)
            production_metadata = {
                "project": self.manifest.model_dump(),
                "analysis": analysis.model_dump(),
                "arrangement": plan.model_dump(),
                "renderer": render.renderer,
                "renderer_metadata": render.metadata,
            }
            exports = finalize_render(
                render.audio_path,
                self.workspace,
                self.manifest.mix,
                production_metadata,
            )

            status.update({
                "stage": "complete",
                "success": True,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exports": exports,
            })
            self._write_status(status)
            return status
        except Exception as exc:
            status.update({
                "stage": "failed",
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            self._write_status(status)
            self.workspace.log(status["traceback"], "failure.log")
            raise

    def analyze_only(self) -> dict:
        analysis = analyze_project(self.workspace, self.manifest)
        plan = build_plan(self.manifest, analysis)
        self.workspace.save_json("analysis.json", analysis.model_dump())
        self.workspace.save_json("arrangement.json", plan.model_dump())
        return {"analysis": analysis.model_dump(), "arrangement": plan.model_dump()}

    def _write_status(self, status: dict) -> None:
        p = self.workspace.root / "aura_status.json"
        p.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
