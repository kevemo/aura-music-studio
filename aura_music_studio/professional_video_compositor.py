from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from .professional_editor_renderer import (
    _AUDIO_SUFFIXES,
    _IMAGE_SUFFIXES,
    _VIDEO_SUFFIXES,
    EditorExportResult,
    EditorRenderError,
    EditorRenderUnsupported,
    ProfessionalEditorRenderer,
)

_SUPPORTED_VIDEO_KEYFRAMES = {
    "transform.x",
    "transform.y",
    "transform.scale_x",
    "transform.scale_y",
    "transform.rotation",
}

_SUPPORTED_VIDEO_ITEM_BLEND_MODES = {
    "normal",
    "multiply",
    "screen",
    "overlay",
    "soft_light",
    "hard_light",
    "darken",
    "lighten",
    "difference",
}

_FFMPEG_VIDEO_BLEND_MODES = {
    "normal": "normal",
    "multiply": "multiply",
    "screen": "screen",
    "overlay": "overlay",
    "soft_light": "softlight",
    "hard_light": "hardlight",
    "darken": "darken",
    "lighten": "lighten",
    "difference": "difference",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean_points(points: list[dict] | None, default: float) -> list[tuple[float, float, str]]:
    rows: list[tuple[float, float, str]] = []
    for point in points or []:
        if not isinstance(point, dict):
            continue
        rows.append((
            max(0.0, _finite(point.get("time"), 0.0)),
            _finite(point.get("value"), default),
            str(point.get("interpolation") or "linear").lower(),
        ))
    by_time = {row[0]: row for row in rows}
    return [by_time[key] for key in sorted(by_time)]


def _keyframe_expr(points: list[dict] | None, default: float, variable: str = "t") -> str:
    """Build a bounded FFmpeg numeric expression matching the still compositor's interpolation."""
    clean = _clean_points(points, default)
    if not clean:
        return f"{float(default):.8f}"
    if len(clean) == 1:
        return f"{clean[0][1]:.8f}"

    tail = f"{clean[-1][1]:.8f}"
    for index in range(len(clean) - 2, -1, -1):
        left_time, left_value, interpolation = clean[index]
        right_time, right_value, _ = clean[index + 1]
        if right_time <= left_time or interpolation == "hold":
            segment = f"{left_value:.8f}"
        else:
            phase = f"(({variable}-{left_time:.8f})/{(right_time-left_time):.8f})"
            if interpolation in {"smooth", "bezier"}:
                phase = f"(({phase})*({phase})*(3-2*({phase})))"
            segment = f"({left_value:.8f}+({right_value-left_value:.8f})*({phase}))"
        tail = (
            f"if(lt({variable},{left_time:.8f}),{left_value:.8f},"
            f"if(lte({variable},{right_time:.8f}),{segment},{tail}))"
        )
    return tail


def _atempo_filters(speed: float) -> list[str]:
    """Split arbitrary editor speed into FFmpeg atempo's safe 0.5..2.0 factors."""
    remaining = max(0.05, min(20.0, float(speed)))
    factors: list[float] = []
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return [f"atempo={factor:.8f}" for factor in factors if abs(factor - 1.0) > 1e-8]


def _transform_default(item: dict[str, Any], name: str, fallback: float) -> float:
    return _finite((item.get("transform") or {}).get(name), fallback)


def _item_keyframes(item: dict[str, Any], path: str) -> list[dict]:
    value = (item.get("keyframes") or {}).get(path) or []
    return value if isinstance(value, list) else []


def _ffmpeg_blend_mode(mode: Any) -> str:
    clean = str(mode or "normal").strip().lower()
    mapped = _FFMPEG_VIDEO_BLEND_MODES.get(clean)
    if mapped is None:
        raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {clean or 'unnamed'}")
    return mapped


class AdvancedVideoCompositor(ProfessionalEditorRenderer):
    """FFmpeg compositor for professional video state that is currently render-safe.

    Supported advanced state is explicit. Any mask/effect/keyframe/blend primitive that has not
    been implemented here still fails closed instead of disappearing from the final export.
    """

    def _validate_video_state(self, sequence: dict[str, Any], tracks: dict, items: dict) -> None:
        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True):
                continue
            if track.get("effects"):
                raise EditorRenderUnsupported("Video track effects are not yet render-safe in the advanced compositor")
            if track.get("keyframes"):
                raise EditorRenderUnsupported("Video track keyframes are not yet render-safe in the advanced compositor")
            if track.get("blend_mode", "normal") != "normal":
                raise EditorRenderUnsupported("Non-normal video track blend modes require grouped track compositing and remain fail-closed")
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True):
                    continue
                if item.get("effects"):
                    raise EditorRenderUnsupported("Video item effects are not yet render-safe in the advanced compositor")
                if item.get("masks"):
                    raise EditorRenderUnsupported("Video masks are not yet render-safe in the advanced compositor")
                blend_mode = str(item.get("blend_mode") or "normal").strip().lower()
                if blend_mode not in _SUPPORTED_VIDEO_ITEM_BLEND_MODES:
                    raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {blend_mode}")
                for path in (item.get("keyframes") or {}):
                    if path not in _SUPPORTED_VIDEO_KEYFRAMES:
                        raise EditorRenderUnsupported(f"Video keyframe path is not yet render-safe: {path}")
                colour = item.get("color") or {}
                if abs(_finite(colour.get("highlights"), 0.0)) > 1e-8 or abs(_finite(colour.get("shadows"), 0.0)) > 1e-8:
                    raise EditorRenderUnsupported("Video item highlights/shadows are not yet render-safe")
                if item.get("kind") in {"adjustment", "generator"} and item.get("visible", True):
                    raise EditorRenderUnsupported(f"Video item kind is not yet render-safe: {item.get('kind')}")

    def _text_asset(self, item: dict[str, Any], work_dir: Path) -> Path:
        layer = self._render_text_layer(item)
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / f"{item['id']}.png"
        layer.save(path, format="PNG")
        return path

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
            raise EditorRenderError("Advanced video compositor requires a video sequence")
        self._validate_sequence(sequence)
        self._validate_video_state(sequence, tracks, items)

        width, height = int(sequence["width"]), int(sequence["height"])
        fps, duration = float(sequence["fps"]), float(sequence["duration"])
        background = self._ffmpeg_color(str(sequence.get("background") or "#000000"))
        work_dir = self.project_dir / "work" / "editor_video" / uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)

        # tuple(track, item, source, input-kind)
        visual: list[tuple[dict, dict, Path, str]] = []
        audio: list[tuple[dict, Path]] = []
        source_refs: list[str] = []
        try:
            for track_id in sequence.get("track_ids", []):
                track = tracks.get(track_id)
                if not track or not track.get("enabled", True):
                    continue
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
                        visual.append((track, item, source, str(kind)))
                        source_refs.append(str(source.relative_to(self.project_dir)))
                        if (
                            kind == "video_clip"
                            and not track.get("muted", False)
                            and not bool((item.get("audio") or {}).get("muted", False))
                            and self._has_audio(source)
                        ):
                            audio.append((item, source))
                    elif kind == "text" and item.get("visible", True) and track.get("visible", True):
                        visual.append((track, item, self._text_asset(item, work_dir / "text"), "text"))
                    elif kind == "audio_clip" and not track.get("muted", False) and not bool((item.get("audio") or {}).get("muted", False)):
                        source = self._source(item.get("source_ref"))
                        if source.suffix.lower() not in (_AUDIO_SUFFIXES | _VIDEO_SUFFIXES):
                            raise EditorRenderUnsupported("Timeline audio item has an unsupported source type")
                        audio.append((item, source))
                        source_refs.append(str(source.relative_to(self.project_dir)))

            args = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"color=c={background}:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)}",
            ]
            input_indexes: dict[str, int] = {}
            next_index = 1
            for _track, item, source, kind in visual:
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

            filters: list[str] = ["[0:v]format=rgba[base0]"]
            current = "base0"
            for index, (track, item, _source, kind) in enumerate(visual, start=1):
                input_index = input_indexes[item["id"]]
                transform, crop, colour = item.get("transform") or {}, item.get("crop") or {}, item.get("color") or {}
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

                opacity = max(0.0, min(1.0, _finite(item.get("opacity"), 1.0) * _finite(track.get("opacity"), 1.0)))
                chain += f"colorchannelmixer=aa={self._ff(opacity)}[layer{index}]"
                filters.append(chain)

                x_default = _transform_default(item, "x", 0.0)
                y_default = _transform_default(item, "y", 0.0)
                x_expr = _keyframe_expr(_item_keyframes(item, "transform.x"), x_default)
                y_expr = _keyframe_expr(_item_keyframes(item, "transform.y"), y_default)
                x = f"(W-w)/2+({x_expr})"
                y = f"(H-h)/2+({y_expr})"
                out = f"base{index}"
                blend_mode = _ffmpeg_blend_mode(item.get("blend_mode", "normal"))
                if blend_mode == "normal":
                    filters.append(
                        f"[{current}][layer{index}]overlay=x='{x}':y='{y}':"
                        f"enable='between(t,{self._ff(start)},{self._ff(start+item_duration)})':eof_action=pass[{out}]"
                    )
                else:
                    filters.append(
                        f"color=c=black@0.0:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)},"
                        f"format=rgba[blendcanvas{index}]"
                    )
                    filters.append(
                        f"[blendcanvas{index}][layer{index}]overlay=x='{x}':y='{y}':"
                        f"enable='between(t,{self._ff(start)},{self._ff(start+item_duration)})':"
                        f"eof_action=pass,format=rgba[placed{index}]"
                    )
                    filters.append(f"[{current}]split=2[basenormal{index}][baseblend{index}]")
                    filters.append(
                        f"[placed{index}]split=3[topnormal{index}][topblend{index}][topmasksrc{index}]"
                    )
                    filters.append(
                        f"[basenormal{index}][topnormal{index}]overlay=0:0:eof_action=pass,"
                        f"format=gbrp[normal{index}]"
                    )
                    filters.append(f"[baseblend{index}]format=gbrp[blendbase{index}]")
                    filters.append(f"[topblend{index}]format=gbrp[blendtop{index}]")
                    filters.append(
                        f"[blendbase{index}][blendtop{index}]blend=all_mode={blend_mode}[blended{index}]"
                    )
                    filters.append(f"[topmasksrc{index}]alphaextract[blendmask{index}]")
                    filters.append(
                        f"[normal{index}][blended{index}][blendmask{index}]maskedmerge,format=rgba[{out}]"
                    )
                current = out

            filters.append(f"[{current}]trim=duration={self._ff(duration)},fps={self._ff(fps)},format=yuv420p[vout]")

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

                fade_in, fade_out = _finite(config.get("fade_in"), 0.0), _finite(config.get("fade_out"), 0.0)
                if fade_in > 0:
                    chain += f",afade=t=in:st=0:d={self._ff(min(fade_in,item_duration))}"
                if fade_out > 0:
                    chain += f",afade=t=out:st={self._ff(max(0,item_duration-fade_out))}:d={self._ff(min(fade_out,item_duration))}"
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
                raise EditorRenderError(f"FFmpeg advanced video render failed: {detail}")
            temporary.replace(output)

            result = self._record_result(
                sequence,
                state,
                output,
                "mp4",
                "ffmpeg-advanced-video-compositor",
                source_refs,
            )
            metadata = self.project_dir / result.metadata_ref
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                payload.update({
                    "advanced_video_compositor": True,
                    "supports_text_layers": True,
                    "supports_reverse_video_audio": True,
                    "supports_transform_keyframes": sorted(_SUPPORTED_VIDEO_KEYFRAMES),
                    "supports_audio_pan": True,
                    "supports_speed_correct_audio": True,
                    "supports_item_blend_modes": sorted(_SUPPORTED_VIDEO_ITEM_BLEND_MODES),
                    "supports_item_temperature_tint": True,
                    "item_temperature_tint_range": [-1.0, 1.0],
                    "item_temperature_tint_preserve_lightness": True,
                    "item_highlights_shadows_fail_closed": True,
                    "track_blend_modes_require_group_compositing": True,
                    "unsupported_state_fails_closed": True,
                })
                metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except Exception:
                pass
            return result
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


__all__ = [
    "AdvancedVideoCompositor",
    "_SUPPORTED_VIDEO_ITEM_BLEND_MODES",
    "_atempo_filters",
    "_ffmpeg_blend_mode",
    "_keyframe_expr",
]
