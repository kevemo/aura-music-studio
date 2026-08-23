from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_project
from .approved_voice_song import apply_approved_voice
from .arrangement import build_plan
from .audio import finalize_render
from .guide import ensure_score_guide
from .layers import build_optional_layers, mix_layers
from .models import ProjectManifest, RenderResult
from .project import ProjectWorkspace
from .provenance import build_provenance, write_provenance
from .quality import evaluate_audio
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

            status["stage"] = "symbolic_control_guide"
            self._write_status(status)
            guide = ensure_score_guide(self.workspace, self.manifest)
            if guide:
                status["guide"] = str(guide)
                status["guide_note"] = "Control/reference only; never exported as finished music."

            status["stage"] = "real_audio_render_and_qc"
            self._write_status(status)
            render, qc, takes = self._render_with_quality_control(plan)

            if (self.manifest.project_dna or {}).get("vocal_mode") == "approved_voice":
                status["stage"] = "approved_voice_conversion"
                self._write_status(status)
                render = apply_approved_voice(render, self.workspace, self.manifest)
                qc = self._evaluate_final_real_audio(render.audio_path, plan)
                status["approved_voice_applied"] = True

            status["stage"] = "real_audio_layers"
            self._write_status(status)
            layers = build_optional_layers(render.audio_path, self.workspace, self.manifest, plan)
            final_real_audio = mix_layers(render.audio_path, layers, self.workspace, self.manifest)
            if layers:
                render = RenderResult(
                    renderer=render.renderer + "+dedicated_layers",
                    audio_path=final_real_audio,
                    audio_origin="hybrid",
                    is_final_quality=True,
                    metadata={
                        **render.metadata,
                        "dedicated_layers": {k: str(v) for k, v in layers.items()},
                    },
                )
                qc = self._evaluate_final_real_audio(render.audio_path, plan)

            status["renderer"] = render.renderer
            status["real_audio_master"] = str(render.audio_path)
            status["audio_origin"] = render.audio_origin
            status["quality"] = qc
            status["takes"] = takes
            status["dedicated_layers"] = {k: str(v) for k, v in layers.items()}
            self.workspace.save_json(
                "render.json",
                {
                    "renderer": render.renderer,
                    "audio_path": str(render.audio_path),
                    "audio_origin": render.audio_origin,
                    "is_final_quality": render.is_final_quality,
                    "metadata": render.metadata,
                    "quality": qc,
                    "takes": takes,
                    "dedicated_layers": {k: str(v) for k, v in layers.items()},
                },
            )

            status["stage"] = "mastering_and_real_audio_stems"
            self._write_status(status)
            production_metadata = {
                "project": self.manifest.model_dump(),
                "analysis": analysis.model_dump(),
                "arrangement": plan.model_dump(),
                "renderer": render.renderer,
                "audio_origin": render.audio_origin,
                "renderer_metadata": render.metadata,
                "quality_control": qc,
                "takes": takes,
                "dedicated_layers": {k: str(v) for k, v in layers.items()},
            }
            exports = finalize_render(
                render.audio_path,
                self.workspace,
                self.manifest.mix,
                production_metadata,
            )

            status["stage"] = "provenance"
            self._write_status(status)
            provenance = build_provenance(
                self.workspace,
                manifest=self.manifest.model_dump(),
                renderer=render.renderer,
                renderer_metadata=render.metadata,
                audio_origin=render.audio_origin,
                quality_control=qc,
                exports=exports,
            )
            provenance_path = write_provenance(self.workspace, provenance)
            exports["provenance"] = str(provenance_path)

            status.update(
                {
                    "stage": "complete",
                    "success": True,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "exports": exports,
                    "provenance_sha256": provenance["integrity"]["canonical_sha256"],
                    "provenance_signed": provenance["integrity"]["signed"],
                }
            )
            self._write_status(status)
            return status
        except Exception as exc:
            status.update(
                {
                    "stage": "failed",
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._write_status(status)
            self.workspace.log(status["traceback"], "failure.log")
            raise

    def _target_duration(self, plan) -> float | None:
        target_duration = self.manifest.target_duration_seconds
        if target_duration is None and self.manifest.total_measures and plan.tempo_bpm:
            beats_per_bar = int(self.manifest.meter.split("/")[0])
            target_duration = self.manifest.total_measures * beats_per_bar * 60.0 / plan.tempo_bpm
        return target_duration

    def _evaluate_final_real_audio(self, path: Path, plan) -> dict:
        if path.name == "score_guide.wav" or "score_guide" in str(path):
            raise RuntimeError("Aura refused a symbolic guide at the final-audio quality stage.")
        qc = evaluate_audio(path, target_duration=self._target_duration(plan), target_bpm=plan.tempo_bpm)
        if not qc["passes_basic_integrity"]:
            raise RuntimeError(f"Final real-audio mix failed integrity QC: {qc}")
        return qc

    def _render_with_quality_control(self, plan):
        target_duration = self._target_duration(plan)
        takes_dir = self.workspace.work_dir / "takes"
        takes_dir.mkdir(parents=True, exist_ok=True)
        attempts = max(1, self.manifest.renderer.quality_retries + 1)
        candidates: list[tuple[float, RenderResult, dict]] = []
        take_records = []

        for index in range(1, attempts + 1):
            render = render_with_failover(self.workspace, self.manifest, plan)
            if self.manifest.renderer.require_real_audio:
                if render.audio_origin == "symbolic_guide" or not render.is_final_quality:
                    raise RuntimeError(
                        f"Aura refused {render.renderer}: symbolic/MIDI guide audio cannot be exported as the final track."
                    )
                if render.audio_path.name == "score_guide.wav" or "score_guide" in str(render.audio_path):
                    raise RuntimeError("Aura refused to export the score/MIDI guide as finished music.")

            take_path = takes_dir / f"take_{index:02d}{render.audio_path.suffix.lower()}"
            shutil.copy2(render.audio_path, take_path)
            take_render = RenderResult(
                renderer=render.renderer,
                audio_path=take_path,
                audio_origin=render.audio_origin,
                is_final_quality=render.is_final_quality,
                metadata=render.metadata,
            )
            qc = evaluate_audio(take_path, target_duration=target_duration, target_bpm=plan.tempo_bpm)
            record = {
                "take": index,
                "renderer": render.renderer,
                "audio_origin": render.audio_origin,
                "path": str(take_path),
                **qc,
            }
            take_records.append(record)
            candidates.append((qc["quality_score"], take_render, qc))
            self.workspace.log(json.dumps(record, default=str), "quality.log")
            if qc["passes_basic_integrity"] and qc["quality_score"] >= self.manifest.renderer.minimum_quality_score:
                break

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, best_render, best_qc = candidates[0]
        if not best_qc["passes_basic_integrity"]:
            raise RuntimeError(f"All real-audio takes failed Aura's integrity gate: {take_records}")
        return best_render, best_qc, take_records

    def analyze_only(self) -> dict:
        analysis = analyze_project(self.workspace, self.manifest)
        plan = build_plan(self.manifest, analysis)
        self.workspace.save_json("analysis.json", analysis.model_dump())
        self.workspace.save_json("arrangement.json", plan.model_dump())
        return {"analysis": analysis.model_dump(), "arrangement": plan.model_dump()}

    def _write_status(self, status: dict) -> None:
        p = self.workspace.root / "aura_status.json"
        p.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
