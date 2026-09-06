from __future__ import annotations

import json
import math
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_video_effects_compositor import _StateProxy
from .professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor
from .professional_video_mask_effects_colour_compositor import UniversalVisualVideoCompositor
from .professional_visual_transitions import (
    EditorVisualTransition,
    transitions_from_sequence,
    validate_visual_transition,
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


class _TransitionAwareVisualMixin:
    """Apply canonical visual transitions as transient alpha-bearing media derivatives.

    Transition strings never come from the caller. The persisted transition resource is parsed by
    the strict Chat 3 model, topology is revalidated against current editor state on every render,
    and only the fixed fade-in/fade-out/cross-dissolve processor set reaches FFmpeg.
    """

    def _transition_items(
        self,
        state: dict[str, Any],
        sequence_id: str,
        transitions: list[EditorVisualTransition],
    ) -> dict[str, list[tuple[str, float]]]:
        branch = state.get("branch") or {}
        sequences = {row.get("id"): row for row in branch.get("sequences", [])}
        items = {row.get("id"): row for row in branch.get("items", [])}
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        rules: dict[str, list[tuple[str, float]]] = {}
        for transition in transitions:
            if not transition.enabled:
                continue
            if transition.easing != "linear":
                raise EditorRenderUnsupported("Only linear visual-transition easing is render-safe in this tranche")
            if transition.kind == "fade_in":
                rules.setdefault(str(transition.to_item_id), []).append(("in", transition.duration))
            elif transition.kind == "fade_out":
                rules.setdefault(str(transition.from_item_id), []).append(("out", transition.duration))
            elif transition.kind == "cross_dissolve":
                # Alpha-ramp only the incoming layer. The outgoing layer remains fully visible
                # underneath until its authored end, yielding a real A*(1-p)+B*p dissolve.
                rules.setdefault(str(transition.to_item_id), []).append(("in", transition.duration))

        for item_id, item_rules in rules.items():
            item = items.get(item_id)
            if item is None:
                raise EditorRenderUnsupported("Visual transition references a missing editor item")
            if item.get("kind") not in {"video_clip", "image_layer"}:
                raise EditorRenderUnsupported("Visual transitions currently render video clips and image layers")
            if item.get("effects"):
                raise EditorRenderUnsupported(
                    "Transitioned items with item effects require the next alpha-preserving effect-composition stage"
                )
            if item.get("masks"):
                raise EditorRenderUnsupported(
                    "Transitioned items with masks require the next alpha-preserving mask-composition stage"
                )
            if abs(_finite((item.get("audio") or {}).get("pan"), 0.0)) > 1e-8:
                raise EditorRenderUnsupported(
                    "Transitioned video items with pan require the next alpha-preserving audio-layout stage"
                )
            if item.get("kind") == "video_clip":
                if bool(item.get("reverse")) or abs(_finite(item.get("speed"), 1.0) - 1.0) > 1e-8:
                    raise EditorRenderUnsupported(
                        "Transitioned speed/reverse video clips require the next transition retiming stage"
                    )
            duration = max(0.0, _finite(item.get("duration"), 0.0))
            if duration <= 0.0:
                raise EditorRenderUnsupported("Transitioned item has no renderable duration")
            for direction, fade_duration in item_rules:
                if fade_duration <= 0.0 or fade_duration > duration:
                    raise EditorRenderUnsupported(f"Invalid {direction} transition duration for editor item")
        return rules

    def _derive_transition_media(
        self,
        item: dict[str, Any],
        rules: list[tuple[str, float]],
        derived_root: Path,
    ) -> str:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")

        source = self._source(item.get("source_ref"))
        duration = max(0.01, _finite(item.get("duration"), 0.01))
        output = derived_root / f"{item['id']}_{uuid4().hex[:10]}.mov"
        output.parent.mkdir(parents=True, exist_ok=True)

        args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if item.get("kind") == "image_layer":
            args += ["-loop", "1", "-t", f"{duration:.8f}", "-i", str(source)]
        else:
            source_in = max(0.0, _finite(item.get("source_in"), 0.0))
            args += ["-ss", f"{source_in:.8f}", "-t", f"{duration:.8f}", "-i", str(source)]

        filters = ["format=rgba"]
        for direction, fade_duration in rules:
            if direction == "in":
                filters.append(f"fade=t=in:st=0:d={fade_duration:.8f}:alpha=1")
            else:
                start = max(0.0, duration - fade_duration)
                filters.append(f"fade=t=out:st={start:.8f}:d={fade_duration:.8f}:alpha=1")
        filters.append("format=argb")

        args += ["-vf", ",".join(filters), "-map", "0:v:0", "-map", "0:a?"]
        args += ["-c:v", "qtrle", "-pix_fmt", "argb"]
        if item.get("kind") == "video_clip":
            args += ["-c:a", "aac", "-b:a", "192k"]
        args += [str(output)]

        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            detail = (completed.stderr or "FFmpeg produced no transition derivative").strip().splitlines()
            tail = detail[-1][:500] if detail else "FFmpeg produced no transition derivative"
            raise EditorRenderError(f"Visual transition render failed: {tail}")
        return output.relative_to(self.project_dir).as_posix()

    def _prepare_transition_state(
        self,
        state: dict[str, Any],
        sequence_id: str,
        derived_root: Path,
    ) -> tuple[dict[str, Any], list[EditorVisualTransition], int]:
        prepared = deepcopy(state)
        branch = prepared.get("branch") or {}
        sequences = {row.get("id"): row for row in branch.get("sequences", [])}
        items = {row.get("id"): row for row in branch.get("items", [])}
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)

        transitions = transitions_from_sequence(sequence)
        for transition in transitions:
            validate_visual_transition(self.store, sequence_id, transition, existing=transitions)
        rules = self._transition_items(prepared, sequence_id, transitions)

        derived_count = 0
        for item_id, item_rules in rules.items():
            item = items[item_id]
            item["source_ref"] = self._derive_transition_media(item, item_rules, derived_root)
            item["source_in"] = 0.0
            item["source_out"] = None
            item["speed"] = 1.0
            item["reverse"] = False
            item["kind"] = "video_clip"
            derived_count += 1
        return prepared, transitions, derived_count

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        real_store = self.store
        original_state = real_store.public_state()
        derived_root = self.project_dir / "work" / "editor_visual_transitions" / uuid4().hex
        try:
            prepared, transitions, derived_count = self._prepare_transition_state(
                original_state, sequence_id, derived_root
            )
            if not transitions:
                return super().render_video_advanced(sequence_id)

            self.store = _StateProxy(prepared)  # type: ignore[assignment]
            result = super().render_video_advanced(sequence_id)
            metadata_path = self.project_dir / result.metadata_ref
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "visual_transition_compositor": True,
                    "visual_transitions_executed": [
                        row.model_dump(mode="json") for row in transitions if row.enabled
                    ],
                    "visual_transition_count": sum(1 for row in transitions if row.enabled),
                    "transition_derivatives": derived_count,
                    "transition_derivatives_ephemeral": True,
                    "supported_visual_transitions": ["fade_in", "fade_out", "cross_dissolve"],
                    "visual_transition_easing": ["linear"],
                    "audio_crossfade_executed": False,
                    "arbitrary_transition_filter_strings": False,
                    "legacy_scene_transition_code_executed": False,
                    "legacy_transition_provenance": "reference-only; rewritten as bounded canonical Chat 3 processors",
                    "source_media_mutated": False,
                }
            )
            metadata_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return result.model_copy(update={"renderer": f"{result.renderer}+visual-transitions"})
        finally:
            self.store = real_store
            shutil.rmtree(derived_root, ignore_errors=True)


class TransitionAwareGroupedVideoCompositor(
    _TransitionAwareVisualMixin, GroupedUnifiedAdvancedVideoCompositor
):
    pass


class TransitionAwareUniversalVisualVideoCompositor(
    _TransitionAwareVisualMixin, UniversalVisualVideoCompositor
):
    pass


__all__ = [
    "TransitionAwareGroupedVideoCompositor",
    "TransitionAwareUniversalVisualVideoCompositor",
]
