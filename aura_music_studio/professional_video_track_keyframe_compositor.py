from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import professional_keyframed_mask_video_compositor as _mask
from . import professional_video_grouped_unified_compositor as _grouped
from .professional_editor_renderer import (
    _AUDIO_SUFFIXES,
    _IMAGE_SUFFIXES,
    _VIDEO_SUFFIXES,
    EditorExportResult,
    EditorRenderError,
    EditorRenderUnsupported,
)
from .professional_video_compositor import (
    _SUPPORTED_VIDEO_ITEM_BLEND_MODES,
    _SUPPORTED_VIDEO_KEYFRAMES,
    _atempo_filters,
    _ffmpeg_blend_mode,
    _finite,
    _item_keyframes,
    _keyframe_expr,
    _transform_default,
)
from .professional_video_effects_compositor import _SUPPORTED_VIDEO_EFFECTS, _video_effect_filter
from .professional_video_track_compositor import (
    GroupedTrackVideoCompositor,
    _SUPPORTED_VIDEO_TRACK_BLEND_MODES,
    _append_full_frame_blend,
    _append_track_effects,
)

SUPPORTED_VIDEO_TRACK_KEYFRAMES = frozenset({"opacity", "track.opacity"})
_ALLOWED_INTERPOLATIONS = frozenset({"hold", "linear", "smooth", "bezier"})


def _track_opacity_keyframes(track: dict[str, Any]) -> list[dict[str, Any]]:
    keyframes = track.get("keyframes") or {}
    if not isinstance(keyframes, dict):
        raise EditorRenderUnsupported("Video track keyframe state must be a mapping")
    unsupported = sorted(str(path) for path in keyframes if path not in SUPPORTED_VIDEO_TRACK_KEYFRAMES)
    if unsupported:
        raise EditorRenderUnsupported("Video track keyframe path is not render-safe: " + ", ".join(unsupported))

    direct = keyframes.get("opacity") or []
    namespaced = keyframes.get("track.opacity") or []
    if direct and namespaced:
        raise EditorRenderUnsupported("Video track opacity automation must use one path, not both opacity and track.opacity")
    points = direct or namespaced
    if not points:
        return []
    if not isinstance(points, list):
        raise EditorRenderUnsupported("Video track opacity keyframes must be a list")

    clean: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            raise EditorRenderUnsupported("Video track opacity keyframe entries must be mappings")
        interpolation = str(point.get("interpolation") or "linear").strip().lower()
        if interpolation not in _ALLOWED_INTERPOLATIONS:
            raise EditorRenderUnsupported(f"Unsupported video track opacity interpolation: {interpolation or 'unnamed'}")
        try:
            value = float(point.get("value"))
            time_value = float(point.get("time", 0.0))
        except (TypeError, ValueError) as exc:
            raise EditorRenderUnsupported("Video track opacity keyframes require numeric time and value") from exc
        if not math.isfinite(value) or not math.isfinite(time_value):
            raise EditorRenderUnsupported("Video track opacity keyframes require finite time and value")
        if value < 0.0 or value > 1.0:
            raise EditorRenderUnsupported("Video track opacity keyframe values must stay between 0 and 1")
        if time_value < 0.0:
            raise EditorRenderUnsupported("Video track opacity keyframe time cannot be negative")
        clean.append({"time": time_value, "value": value, "interpolation": interpolation})
    return clean


def _validate_grouped_state_with_track_opacity(self, state: dict[str, Any], sequence_id: str) -> None:
    sequences, tracks, items = self._branch_maps(state)
    sequence = sequences.get(sequence_id)
    if sequence is None:
        raise KeyError(sequence_id)

    for track_id in sequence.get("track_ids", []):
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        _track_opacity_keyframes(track)
        for effect in track.get("effects") or []:
            if not effect.get("enabled", True):
                continue
            _video_effect_filter(effect)
            if effect.get("keyframes"):
                raise EditorRenderUnsupported("Keyframed video track effects remain fail-closed")
        track_blend = str(track.get("blend_mode") or "normal").strip().lower()
        if track_blend not in _SUPPORTED_VIDEO_TRACK_BLEND_MODES:
            raise EditorRenderUnsupported(f"Video track blend mode is not render-safe: {track_blend}")

        for item_id in track.get("item_ids", []):
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            blend_mode = str(item.get("blend_mode") or "normal").strip().lower()
            if blend_mode not in _SUPPORTED_VIDEO_ITEM_BLEND_MODES:
                raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {blend_mode}")
            effects = [effect for effect in item.get("effects") or [] if effect.get("enabled", True)]
            masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]
            for effect in effects:
                _video_effect_filter(effect)
            for mask in masks:
                shape = str(mask.get("shape") or "").lower()
                if shape not in _grouped._SUPPORTED_STATIC_MASK_SHAPES:
                    raise EditorRenderUnsupported(f"Unsupported video mask shape: {shape or 'unnamed'}")
                _mask._validate_mask_keyframes(mask, item_kind=str(item.get("kind") or ""))
            if masks and not _grouped._crop_is_default(item):
                raise EditorRenderUnsupported("Video masks combined with crop are not yet render-safe")
            if masks and effects and not _grouped._colour_is_default(item):
                raise EditorRenderUnsupported(
                    "Video masks combined with item effects and colour adjustments are not yet render-safe"
                )


class TrackOpacityKeyframedGroupedTrackVideoCompositor(GroupedTrackVideoCompositor):
    """Grouped Video Studio compositor with authored track-opacity automation."""

    def _validate_video_state(self, sequence: dict[str, Any], tracks: dict, items: dict) -> None:
        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True):
                continue
            _track_opacity_keyframes(track)
            for effect in track.get("effects") or []:
                if not effect.get("enabled", True):
                    continue
                _video_effect_filter(effect)
                if effect.get("keyframes"):
                    raise EditorRenderUnsupported("Keyframed video track effects remain fail-closed")
            track_blend = str(track.get("blend_mode") or "normal").strip().lower()
            if track_blend not in _SUPPORTED_VIDEO_TRACK_BLEND_MODES:
                raise EditorRenderUnsupported(f"Video track blend mode is not render-safe: {track_blend}")

            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True):
                    continue
                if item.get("effects"):
                    raise EditorRenderUnsupported("Video item effects must be pre-rendered before grouped composition")
                if item.get("masks"):
                    raise EditorRenderUnsupported("Video masks must be pre-rendered before grouped composition")
                blend_mode = str(item.get("blend_mode") or "normal").strip().lower()
                if blend_mode not in _SUPPORTED_VIDEO_ITEM_BLEND_MODES:
                    raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {blend_mode}")
                for path in (item.get("keyframes") or {}):
                    if path not in _SUPPORTED_VIDEO_KEYFRAMES:
                        raise EditorRenderUnsupported(f"Video keyframe path is not yet render-safe: {path}")
                if item.get("kind") in {"adjustment", "generator"} and item.get("visible", True):
                    raise EditorRenderUnsupported(f"Video item kind is not yet render-safe: {item.get('kind')}")

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")

        state = self.store.public_state()
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence["kind"] != "video":
            raise EditorRenderError("Grouped track compositor requires a video sequence")
        self._validate_sequence(sequence)
        self._validate_video_state(sequence, tracks, items)

        width, height = int(sequence["width"]), int(sequence["height"])
        fps, duration = float(sequence["fps"]), float(sequence["duration"])
        background = self._ffmpeg_color(str(sequence.get("background") or "#000000"))
        work_dir = self.project_dir / "work" / "editor_video_tracks" / uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)

        visual_tracks: list[tuple[dict, list[tuple[dict, Path, str]]]] = []
        audio: list[tuple[dict, Path]] = []
        source_refs: list[str] = []
        try:
            for track_id in sequence.get("track_ids", []):
                track = tracks.get(track_id)
                if not track or not track.get("enabled", True):
                    continue
                track_visual: list[tuple[dict, Path, str]] = []
                for item_id in track.get("item_ids", []):
                    item = items.get(item_id)
                    if not item or not item.get("enabled", True):
                        continue
                    kind = item.get("kind")
                    if kind in {"video_clip", "image_layer"} and item.get("visible", True) and track.get("visible", True):
                        source = self._source(item.get("source_ref"))
                        allowed = _VIDEO_SUFFIXES if kind == "video_clip" else _IMAGE_SUFFIXES
                        if source.suffix.lower() not in allowed:
                            raise EditorRenderUnsupported("Timeline visual item has an unsupported source type")
                        track_visual.append((item, source, str(kind)))
                        source_refs.append(str(source.relative_to(self.project_dir)))
                        if (
                            kind == "video_clip"
                            and not track.get("muted", False)
                            and not bool((item.get("audio") or {}).get("muted", False))
                            and self._has_audio(source)
                        ):
                            audio.append((item, source))
                    elif kind == "text" and item.get("visible", True) and track.get("visible", True):
                        track_visual.append((item, self._text_asset(item, work_dir / "text"), "text"))
                    elif kind == "audio_clip" and not track.get("muted", False) and not bool((item.get("audio") or {}).get("muted", False)):
                        source = self._source(item.get("source_ref"))
                        if source.suffix.lower() not in (_AUDIO_SUFFIXES | _VIDEO_SUFFIXES):
                            raise EditorRenderUnsupported("Timeline audio item has an unsupported source type")
                        audio.append((item, source))
                        source_refs.append(str(source.relative_to(self.project_dir)))
                if track_visual:
                    visual_tracks.append((track, track_visual))

            args = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"color=c={background}:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)}",
            ]
            input_indexes: dict[str, int] = {}
            next_index = 1
            for _track_state, entries in visual_tracks:
                for item, source, kind in entries:
                    if kind in {"image_layer", "text"}:
                        args += ["-loop", "1", "-t", self._ff(float(item["duration"])), "-i", str(source)]
                    else:
                        args += ["-i", str(source)]
                    input_indexes[item["id"]] = next_index
                    next_index += 1

            audio_indexes: list[tuple[int, dict]] = []
            for item, source in audio:
                args += ["-i", str(source)]
                audio_indexes.append((next_index, item))
                next_index += 1

            filters: list[str] = ["[0:v]format=rgba[sequencebase0]"]
            sequence_current = "sequencebase0"
            layer_index = 0
            track_effects_applied = 0
            track_opacity_keyframed = 0

            for track_index, (track, entries) in enumerate(visual_tracks, start=1):
                filters.append(
                    f"color=c=black@0.0:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)},"
                    f"format=rgba[track{track_index}base0]"
                )
                track_current = f"track{track_index}base0"

                for item, _source, kind in entries:
                    layer_index += 1
                    input_index = input_indexes[item["id"]]
                    crop = item.get("crop") or {}
                    colour = item.get("color") or {}
                    left, top = _finite(crop.get("left"), 0.0), _finite(crop.get("top"), 0.0)
                    right, bottom = _finite(crop.get("right"), 0.0), _finite(crop.get("bottom"), 0.0)
                    start, item_duration = float(item["start"]), float(item["duration"])
                    source_in = float(item.get("source_in") or 0.0)
                    speed = max(0.05, min(20.0, float(item.get("speed") or 1.0)))
                    chain = f"[{input_index}:v]"
                    if kind == "video_clip":
                        chain += f"trim=start={self._ff(source_in)}:duration={self._ff(item_duration * speed)},"
                        if item.get("reverse"):
                            chain += "reverse,"
                        chain += f"setpts=(PTS-STARTPTS)/{self._ff(speed)}+{self._ff(start)}/TB,"
                    else:
                        chain += f"trim=duration={self._ff(item_duration)},setpts=PTS-STARTPTS+{self._ff(start)}/TB,"
                    chain += (
                        f"crop=iw*{self._ff(1-left-right)}:ih*{self._ff(1-top-bottom)}:"
                        f"iw*{self._ff(left)}:ih*{self._ff(top)},"
                    )

                    sx_default = _transform_default(item, "scale_x", 1.0)
                    sy_default = _transform_default(item, "scale_y", 1.0)
                    sx_expr = _keyframe_expr(_item_keyframes(item, "transform.scale_x"), sx_default)
                    sy_expr = _keyframe_expr(_item_keyframes(item, "transform.scale_y"), sy_default)
                    if _item_keyframes(item, "transform.scale_x") or _item_keyframes(item, "transform.scale_y"):
                        chain += f"scale=w='iw*({sx_expr})':h='ih*({sy_expr})':eval=frame,"
                    else:
                        chain += f"scale=iw*{self._ff(sx_default)}:ih*{self._ff(sy_default)},"

                    brightness = _finite(colour.get("brightness"), 0.0) + _finite(colour.get("exposure"), 0.0) * 0.1
                    temperature = max(-1.0, min(1.0, _finite(colour.get("temperature"), 0.0)))
                    tint = max(-1.0, min(1.0, _finite(colour.get("tint"), 0.0)))
                    warmth = temperature * 0.24
                    green = -tint * 0.20
                    chain += (
                        f"eq=brightness={self._ff(max(-1.0,min(1.0,brightness)))}:"
                        f"contrast={self._ff(max(0.0,_finite(colour.get('contrast'),1.0)))}:"
                        f"saturation={self._ff(max(0.0,_finite(colour.get('saturation'),1.0)))}:"
                        f"gamma={self._ff(max(.05,_finite(colour.get('gamma'),1.0)))},"
                    )
                    if abs(temperature) > 1e-8 or abs(tint) > 1e-8:
                        chain += (
                            f"colorbalance=rm={self._ff(warmth)}:gm={self._ff(green)}:bm={self._ff(-warmth)}:"
                            f"rh={self._ff(warmth)}:gh={self._ff(green)}:bh={self._ff(-warmth)}:pl=1,"
                        )
                    chain += "format=rgba,"

                    rotation_default = _transform_default(item, "rotation", 0.0)
                    rotation_points = _item_keyframes(item, "transform.rotation")
                    rotation_expr = _keyframe_expr(rotation_points, rotation_default)
                    if rotation_points or abs(rotation_default) > 1e-8:
                        chain += (
                            f"rotate='({rotation_expr})*PI/180':c=none:"
                            "ow='hypot(iw,ih)':oh='hypot(iw,ih)',"
                        )

                    item_opacity = max(0.0, min(1.0, _finite(item.get("opacity"), 1.0)))
                    chain += f"colorchannelmixer=aa={self._ff(item_opacity)}[layer{layer_index}]"
                    filters.append(chain)

                    x_default = _transform_default(item, "x", 0.0)
                    y_default = _transform_default(item, "y", 0.0)
                    x_expr = _keyframe_expr(_item_keyframes(item, "transform.x"), x_default)
                    y_expr = _keyframe_expr(_item_keyframes(item, "transform.y"), y_default)
                    x = f"(W-w)/2+({x_expr})"
                    y = f"(H-h)/2+({y_expr})"
                    item_out = f"track{track_index}item{layer_index}"
                    item_mode = _ffmpeg_blend_mode(item.get("blend_mode", "normal"))
                    if item_mode == "normal":
                        filters.append(
                            f"[{track_current}][layer{layer_index}]overlay=x='{x}':y='{y}':"
                            f"enable='between(t,{self._ff(start)},{self._ff(start+item_duration)})':"
                            f"eof_action=pass,format=rgba[{item_out}]"
                        )
                    else:
                        placed = f"track{track_index}placed{layer_index}"
                        filters.append(
                            f"color=c=black@0.0:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)},"
                            f"format=rgba[track{track_index}blendcanvas{layer_index}]"
                        )
                        filters.append(
                            f"[track{track_index}blendcanvas{layer_index}][layer{layer_index}]"
                            f"overlay=x='{x}':y='{y}':enable='between(t,{self._ff(start)},{self._ff(start+item_duration)})':"
                            f"eof_action=pass,format=rgba[{placed}]"
                        )
                        _append_full_frame_blend(
                            filters,
                            base=track_current,
                            top=placed,
                            mode=item_mode,
                            out=item_out,
                            suffix=f"t{track_index}i{layer_index}",
                        )
                    track_current = item_out

                track_current, applied_effects = _append_track_effects(
                    filters,
                    input_label=track_current,
                    effects=list(track.get("effects") or []),
                    track_index=track_index,
                )
                track_effects_applied += applied_effects

                track_opacity = max(0.0, min(1.0, _finite(track.get("opacity"), 1.0)))
                opacity_points = _track_opacity_keyframes(track)
                track_final = f"track{track_index}final"
                if opacity_points:
                    track_opacity_keyframed += 1
                    opacity_expr = _keyframe_expr(opacity_points, track_opacity, variable="T")
                    bounded = f"min(1,max(0,({opacity_expr})))"
                    filters.append(
                        f"[{track_current}]format=rgba,"
                        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({bounded})'[{track_final}]"
                    )
                else:
                    filters.append(
                        f"[{track_current}]format=rgba,colorchannelmixer=aa={self._ff(track_opacity)}[{track_final}]"
                    )
                sequence_out = f"sequencebase{track_index}"
                _append_full_frame_blend(
                    filters,
                    base=sequence_current,
                    top=track_final,
                    mode=str(track.get("blend_mode") or "normal"),
                    out=sequence_out,
                    suffix=f"trackblend{track_index}",
                )
                sequence_current = sequence_out

            filters.append(
                f"[{sequence_current}]trim=duration={self._ff(duration)},fps={self._ff(fps)},format=yuv420p[vout]"
            )

            audio_labels: list[str] = []
            for index, (input_index, item) in enumerate(audio_indexes):
                config = item.get("audio") or {}
                source_in = float(item.get("source_in") or 0.0)
                item_duration = float(item["duration"])
                start = float(item["start"])
                speed = max(0.05, min(20.0, float(item.get("speed") or 1.0)))
                gain = 10.0 ** (_finite(config.get("gain_db"), 0.0) / 20.0)
                chain = (
                    f"[{input_index}:a]atrim=start={self._ff(source_in)}:"
                    f"duration={self._ff(item_duration * speed)},"
                )
                if item.get("reverse"):
                    chain += "areverse,"
                chain += "asetpts=PTS-STARTPTS"
                for atempo in _atempo_filters(speed):
                    chain += f",{atempo}"
                chain += f",volume={self._ff(gain)}"

                pan = max(-1.0, min(1.0, _finite(config.get("pan"), 0.0)))
                if abs(pan) > 1e-8:
                    left_gain = math.sqrt(max(0.0, 1.0 - pan)) if pan > 0 else 1.0
                    right_gain = math.sqrt(max(0.0, 1.0 + pan)) if pan < 0 else 1.0
                    chain += f",pan=stereo|c0={self._ff(left_gain)}*c0|c1={self._ff(right_gain)}*c1"

                fade_in = _finite(config.get("fade_in"), 0.0)
                fade_out = _finite(config.get("fade_out"), 0.0)
                if fade_in > 0:
                    chain += f",afade=t=in:st=0:d={self._ff(min(fade_in,item_duration))}"
                if fade_out > 0:
                    chain += (
                        f",afade=t=out:st={self._ff(max(0,item_duration-fade_out))}:"
                        f"d={self._ff(min(fade_out,item_duration))}"
                    )
                chain += f",adelay={max(0,int(round(start*1000)))}:all=1[a{index}]"
                filters.append(chain)
                audio_labels.append(f"[a{index}]")

            if audio_labels:
                filters.append(
                    "".join(audio_labels)
                    + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0,"
                    + f"atrim=duration={self._ff(duration)}[aout]"
                )

            filename = f"{sequence_id}_{uuid4().hex[:12]}.mp4"
            output = self._output(filename)
            temporary = output.with_name(output.stem + ".part.mp4")
            args += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
            if audio_labels:
                args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
            else:
                args += ["-an"]
            args += [
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
                "-t", self._ff(duration), str(temporary),
            ]

            completed = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout_seconds, check=False)
            if completed.returncode != 0 or not temporary.is_file():
                temporary.unlink(missing_ok=True)
                detail = (completed.stderr or "FFmpeg produced no export").strip().splitlines()[-1][:500]
                raise EditorRenderError(f"FFmpeg grouped-track video render failed: {detail}")
            temporary.replace(output)

            result = self._record_result(
                sequence,
                state,
                output,
                "mp4",
                "ffmpeg-grouped-track-opacity-keyframe-compositor" if track_opacity_keyframed else "ffmpeg-grouped-track-video-compositor",
                source_refs,
            )
            metadata = self.project_dir / result.metadata_ref
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                payload.update({
                    "advanced_video_compositor": True,
                    "grouped_track_compositor": True,
                    "supports_text_layers": True,
                    "supports_reverse_video_audio": True,
                    "supports_transform_keyframes": sorted(_SUPPORTED_VIDEO_KEYFRAMES),
                    "supports_audio_pan": True,
                    "supports_speed_correct_audio": True,
                    "supports_item_blend_modes": sorted(_SUPPORTED_VIDEO_ITEM_BLEND_MODES),
                    "supports_track_blend_modes": sorted(_SUPPORTED_VIDEO_TRACK_BLEND_MODES),
                    "supports_track_effects": sorted(_SUPPORTED_VIDEO_EFFECTS),
                    "track_effects_applied": track_effects_applied,
                    "track_effects_applied_before_opacity_and_blend": True,
                    "track_opacity_applied_after_item_composition": True,
                    "track_opacity_applied_after_track_effects": True,
                    "supported_video_track_keyframes": sorted(SUPPORTED_VIDEO_TRACK_KEYFRAMES),
                    "track_opacity_keyframed_groups": track_opacity_keyframed,
                    "track_opacity_keyframes_execute_in_sequence_time": True,
                    "track_opacity_keyframes_fail_closed": False,
                    "unsupported_track_keyframes_fail_closed": True,
                    "track_effects_fail_closed": False,
                    "keyframed_track_effects_fail_closed": True,
                    "source_media_mutated": False,
                    "unsupported_state_fails_closed": True,
                })
                metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except Exception:
                pass
            return result
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


# The grouped unified compositor resolves this module-global delegate at render time. Install the
# keyframe-capable delegate only when this production extension is imported, leaving older direct
# imports unchanged and preserving a narrow activation surface.
_grouped.GroupedTrackVideoCompositor = TrackOpacityKeyframedGroupedTrackVideoCompositor
_grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state = _validate_grouped_state_with_track_opacity


def _count_track_opacity_keyframes(state: dict[str, Any], sequence_id: str) -> int:
    branch = state.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return 0
    count = 0
    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if track and track.get("enabled", True) and _track_opacity_keyframes(track):
            count += 1
    return count


class TrackKeyframeUniversalVisualVideoCompositor(_mask.KeyframedMaskUniversalVisualVideoCompositor):
    """Production Video Studio compositor with grouped track-opacity automation."""

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        state = self.store.public_state()
        keyframed_tracks = _count_track_opacity_keyframes(state, sequence_id)
        result = super().render_video_advanced(sequence_id)
        metadata = self.project_dir / result.metadata_ref
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload.update({
            "professional_track_keyframe_compositor": keyframed_tracks > 0,
            "professional_track_opacity_keyframed_groups": keyframed_tracks,
            "professional_track_keyframe_paths_supported": sorted(SUPPORTED_VIDEO_TRACK_KEYFRAMES),
            "professional_track_opacity_stage": "after_track_effects_before_track_blend",
            "professional_track_opacity_source_media_mutated": False,
            "unsupported_track_keyframes_fail_closed": True,
            "keyframed_track_effects_fail_closed": True,
        })
        metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if keyframed_tracks:
            return result.model_copy(update={"renderer": "ffmpeg-universal-track-opacity-keyframe-video-compositor"})
        return result


UniversalVisualVideoCompositor = TrackKeyframeUniversalVisualVideoCompositor


__all__ = [
    "SUPPORTED_VIDEO_TRACK_KEYFRAMES",
    "TrackOpacityKeyframedGroupedTrackVideoCompositor",
    "TrackKeyframeUniversalVisualVideoCompositor",
    "UniversalVisualVideoCompositor",
    "_track_opacity_keyframes",
]
