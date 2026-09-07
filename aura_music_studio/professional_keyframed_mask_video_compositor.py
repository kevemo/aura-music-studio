from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

from . import professional_video_effects_compositor as _effects
from . import professional_video_grouped_unified_compositor as _grouped
from .professional_chroma_key_video_compositor import (
    ChromaKeyUniversalVisualVideoCompositor,
)
from .professional_editor_renderer import EditorExportResult, EditorRenderError, EditorRenderUnsupported
from .professional_image_compositor import _apply_masks


SUPPORTED_KEYFRAMED_MASK_PATHS = frozenset({"points", "opacity", "feather", "expansion"})


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _strict_finite(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EditorRenderUnsupported(f"Keyframed video mask {label} must be numeric") from exc
    if not math.isfinite(number):
        raise EditorRenderUnsupported(f"Keyframed video mask {label} must be finite")
    return number


def _clean_keyframes(points: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(points, list):
        raise EditorRenderUnsupported(f"Keyframed video mask {label} points must be a list")
    clean: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            raise EditorRenderUnsupported(f"Keyframed video mask {label} entries must be mappings")
        interpolation = str(point.get("interpolation") or "linear").strip().lower()
        if interpolation not in {"hold", "linear", "smooth", "bezier"}:
            raise EditorRenderUnsupported(
                f"Unsupported keyframed video mask interpolation for {label}: {interpolation or 'unnamed'}"
            )
        clean.append(
            {
                "time": max(0.0, _strict_finite(point.get("time", 0.0), label=f"{label} time")),
                "value": point.get("value"),
                "interpolation": interpolation,
            }
        )
    by_time = {row["time"]: row for row in clean}
    return [by_time[key] for key in sorted(by_time)]


def _mask_points(value: Any, *, label: str = "points") -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        raise EditorRenderUnsupported(f"Keyframed video mask {label} must be a point list")
    result: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise EditorRenderUnsupported(
                f"Keyframed video mask {label} entry {index} must contain exactly x and y"
            )
        x = max(0.0, min(1.0, _strict_finite(point[0], label=f"{label} x")))
        y = max(0.0, min(1.0, _strict_finite(point[1], label=f"{label} y")))
        result.append((x, y))
    return result


def _phase(interpolation: str, value: float) -> float:
    value = max(0.0, min(1.0, value))
    if interpolation in {"smooth", "bezier"}:
        return value * value * (3.0 - 2.0 * value)
    return value


def _numeric_keyframe_value(points: Any, time_value: float, default: float, *, label: str) -> float:
    clean = _clean_keyframes(points, label=label)
    if not clean:
        return default
    if time_value <= clean[0]["time"]:
        return _strict_finite(clean[0]["value"], label=label)
    if time_value >= clean[-1]["time"]:
        return _strict_finite(clean[-1]["value"], label=label)
    for left, right in zip(clean, clean[1:]):
        if left["time"] <= time_value <= right["time"]:
            left_value = _strict_finite(left["value"], label=label)
            if left["interpolation"] == "hold" or right["time"] <= left["time"]:
                return left_value
            right_value = _strict_finite(right["value"], label=label)
            amount = _phase(
                left["interpolation"],
                (time_value - left["time"]) / (right["time"] - left["time"]),
            )
            return left_value + (right_value - left_value) * amount
    return default


def _points_keyframe_value(points: Any, time_value: float, default: Any) -> list[tuple[float, float]]:
    clean = _clean_keyframes(points, label="points")
    default_points = _mask_points(default, label="base points")
    if not clean:
        return default_points
    if time_value <= clean[0]["time"]:
        return _mask_points(clean[0]["value"])
    if time_value >= clean[-1]["time"]:
        return _mask_points(clean[-1]["value"])
    for left, right in zip(clean, clean[1:]):
        if left["time"] <= time_value <= right["time"]:
            left_points = _mask_points(left["value"])
            if left["interpolation"] == "hold" or right["time"] <= left["time"]:
                return left_points
            right_points = _mask_points(right["value"])
            if len(left_points) != len(right_points):
                raise EditorRenderUnsupported(
                    "Interpolated video mask point keyframes must keep the same control-point count; "
                    "use hold interpolation when intentionally changing topology"
                )
            amount = _phase(
                left["interpolation"],
                (time_value - left["time"]) / (right["time"] - left["time"]),
            )
            return [
                (
                    max(0.0, min(1.0, lx + (rx - lx) * amount)),
                    max(0.0, min(1.0, ly + (ry - ly) * amount)),
                )
                for (lx, ly), (rx, ry) in zip(left_points, right_points)
            ]
    return default_points


def _validate_mask_keyframes(mask: dict[str, Any], *, item_kind: str) -> None:
    keyframes = mask.get("keyframes") or {}
    if not isinstance(keyframes, dict):
        raise EditorRenderUnsupported("Keyframed video mask state must be a mapping")
    active_paths = {str(name) for name, rows in keyframes.items() if rows}
    unsupported = sorted(active_paths - SUPPORTED_KEYFRAMED_MASK_PATHS)
    if unsupported:
        raise EditorRenderUnsupported(
            "Unsupported keyframed video mask path: " + ", ".join(unsupported)
        )
    if active_paths and item_kind != "video_clip":
        raise EditorRenderUnsupported("Keyframed video masks currently require a video clip")
    if mask.get("tracking"):
        raise EditorRenderUnsupported(
            "Automatic/tracked video masks are not yet render-safe; authored mask keyframes are supported separately"
        )

    for name in sorted(active_paths):
        rows = _clean_keyframes(keyframes.get(name), label=name)
        if name == "points":
            parsed = [_mask_points(row["value"]) for row in rows]
            for index, (left, right) in enumerate(zip(rows, rows[1:])):
                if left["interpolation"] != "hold" and len(parsed[index]) != len(parsed[index + 1]):
                    raise EditorRenderUnsupported(
                        "Interpolated video mask point keyframes must keep the same control-point count; "
                        "use hold interpolation when intentionally changing topology"
                    )
        else:
            for row in rows:
                _strict_finite(row["value"], label=name)


def _mask_state_at_time(mask: dict[str, Any], sequence_time: float) -> dict[str, Any]:
    state = deepcopy(mask)
    keyframes = mask.get("keyframes") or {}
    if not keyframes:
        return state
    _validate_mask_keyframes(mask, item_kind="video_clip")

    if keyframes.get("points"):
        state["points"] = _points_keyframe_value(
            keyframes["points"],
            sequence_time,
            mask.get("points") or [],
        )
    if keyframes.get("opacity"):
        state["opacity"] = max(
            0.0,
            min(
                1.0,
                _numeric_keyframe_value(
                    keyframes["opacity"], sequence_time, _finite(mask.get("opacity"), 1.0), label="opacity"
                ),
            ),
        )
    if keyframes.get("feather"):
        state["feather"] = max(
            0.0,
            min(
                1000.0,
                _numeric_keyframe_value(
                    keyframes["feather"], sequence_time, _finite(mask.get("feather"), 0.0), label="feather"
                ),
            ),
        )
    if keyframes.get("expansion"):
        state["expansion"] = max(
            -1000.0,
            min(
                1000.0,
                _numeric_keyframe_value(
                    keyframes["expansion"], sequence_time, _finite(mask.get("expansion"), 0.0), label="expansion"
                ),
            ),
        )
    state["keyframes"] = {}
    return state


def _sequence_time_for_mask_frame(item: dict[str, Any], derivative_time: float) -> float:
    start = max(0.0, _finite(item.get("start"), 0.0))
    item_duration = max(0.01, _finite(item.get("duration"), 0.01))
    speed = max(0.05, min(20.0, _finite(item.get("speed"), 1.0)))
    timeline_delta = max(0.0, derivative_time) / speed
    if item.get("reverse"):
        return max(start, start + item_duration - timeline_delta)
    return min(start + item_duration, start + timeline_delta)


def _mask_alpha_frame(item: dict[str, Any], size: tuple[int, int], derivative_time: float) -> Image.Image:
    sequence_time = _sequence_time_for_mask_frame(item, derivative_time)
    masks = [
        _mask_state_at_time(mask, sequence_time)
        for mask in item.get("masks") or []
        if mask.get("enabled", True)
    ]
    opaque = Image.new("RGBA", size, (255, 255, 255, 255))
    return _apply_masks(opaque, masks).getchannel("A")


def _parse_rate(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            rate = float(numerator) / denominator_value
        else:
            rate = float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(rate) or rate <= 0:
        return None
    return max(1.0, min(120.0, rate))


def _video_frame_rate(source: Path, *, timeout_seconds: float) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 30.0
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=min(timeout_seconds, 60.0),
        check=False,
    )
    if completed.returncode == 0:
        try:
            streams = json.loads(completed.stdout or "{}").get("streams") or []
            if streams:
                for name in ("avg_frame_rate", "r_frame_rate"):
                    rate = _parse_rate(streams[0].get(name))
                    if rate is not None:
                        return rate
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return 30.0


def _has_keyframed_masks(item: dict[str, Any]) -> bool:
    return any(
        bool(mask.get("enabled", True) and any((mask.get("keyframes") or {}).values()))
        for mask in item.get("masks") or []
    )


_ORIGINAL_DERIVE_VIDEO = getattr(
    _effects.VideoItemEffectsCompositor._derive_video,
    "_aura_original_keyframed_mask_derive_video",
    _effects.VideoItemEffectsCompositor._derive_video,
)
_ORIGINAL_GROUPED_VALIDATE = getattr(
    _grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state,
    "_aura_original_keyframed_mask_validate",
    _grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state,
)


def _validate_grouped_state_with_keyframed_masks(self, state: dict[str, Any], sequence_id: str) -> None:
    sequences, tracks, items = self._branch_maps(state)
    sequence = sequences.get(sequence_id)
    if sequence is None:
        raise KeyError(sequence_id)

    for track_id in sequence.get("track_ids", []):
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for effect in track.get("effects") or []:
            if effect.get("enabled", True):
                _grouped._video_effect_filter(effect)
        if track.get("keyframes"):
            raise EditorRenderUnsupported("Video track keyframes require the next grouped-track automation stage")
        track_blend = str(track.get("blend_mode") or "normal").strip().lower()
        if track_blend not in _grouped._SUPPORTED_VIDEO_TRACK_BLEND_MODES:
            raise EditorRenderUnsupported(f"Video track blend mode is not render-safe: {track_blend}")

        for item_id in track.get("item_ids", []):
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            blend_mode = str(item.get("blend_mode") or "normal").strip().lower()
            if blend_mode not in _grouped._SUPPORTED_VIDEO_ITEM_BLEND_MODES:
                raise EditorRenderUnsupported(f"Video item blend mode is not render-safe: {blend_mode}")

            effects = [effect for effect in item.get("effects") or [] if effect.get("enabled", True)]
            masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]
            for effect in effects:
                _grouped._video_effect_filter(effect)
            for mask in masks:
                shape = str(mask.get("shape") or "").lower()
                if shape not in _grouped._SUPPORTED_STATIC_MASK_SHAPES:
                    raise EditorRenderUnsupported(f"Unsupported video mask shape: {shape or 'unnamed'}")
                _validate_mask_keyframes(mask, item_kind=str(item.get("kind") or ""))

            if masks and not _grouped._crop_is_default(item):
                raise EditorRenderUnsupported("Video masks combined with crop are not yet render-safe")
            if masks and effects and not _grouped._colour_is_default(item):
                raise EditorRenderUnsupported(
                    "Video masks combined with item effects and colour adjustments are not yet render-safe"
                )


setattr(
    _validate_grouped_state_with_keyframed_masks,
    "_aura_original_keyframed_mask_validate",
    _ORIGINAL_GROUPED_VALIDATE,
)


def _derive_video_with_keyframed_masks(
    self,
    item: dict[str, Any],
    derived_root: Path,
    *,
    force_stereo: bool,
) -> str:
    masks = [mask for mask in item.get("masks") or [] if mask.get("enabled", True)]
    if not masks:
        return _ORIGINAL_DERIVE_VIDEO(self, item, derived_root, force_stereo=force_stereo)

    for mask in masks:
        _validate_mask_keyframes(mask, item_kind=str(item.get("kind") or ""))
    if item.get("kind") != "video_clip":
        return _ORIGINAL_DERIVE_VIDEO(self, item, derived_root, force_stereo=force_stereo)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
    source = self._source(item.get("source_ref"))
    output = derived_root / f"{item['id']}.mov"
    output.parent.mkdir(parents=True, exist_ok=True)
    source_in = max(0.0, _effects._finite(item.get("source_in"), 0.0))
    speed = max(0.05, min(20.0, _effects._finite(item.get("speed"), 1.0)))
    duration = max(0.01, _effects._finite(item.get("duration"), 0.01) * speed)
    graph, label = _effects._video_effect_graph(list(item.get("effects") or []))
    width, height = self._video_dimensions(source, derived_root)
    animated = _has_keyframed_masks(item)

    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{source_in:.8f}",
        "-t",
        f"{duration:.8f}",
        "-i",
        str(source),
    ]
    filters: list[str] = []
    if graph:
        filters.append(graph)
    current = label if graph else "0:v"

    mask_fps = _video_frame_rate(source, timeout_seconds=self.timeout_seconds)
    mask_asset: Path | None = None
    if animated:
        args += [
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            f"{mask_fps:.8f}",
            "-i",
            "pipe:0",
        ]
    else:
        mask_asset = self._mask_asset(item, source, derived_root)
        args += [
            "-loop",
            "1",
            "-framerate",
            f"{mask_fps:.8f}",
            "-t",
            f"{duration:.8f}",
            "-i",
            str(mask_asset),
        ]

    # Preserve any alpha already present in the source derivative (for example Wave 7 chroma key)
    # and multiply it by the authored mask rather than replacing the existing matte.
    filters.extend(
        [
            f"[{current}]format=rgba,split=2[maskcolor][maskalphasrc]",
            "[maskalphasrc]alphaextract[basealpha]",
            "[1:v]format=gray[authoredalpha]",
            "[basealpha][authoredalpha]blend=all_mode=multiply[combinedalpha]",
            "[maskcolor][combinedalpha]alphamerge,format=argb[vfinal]",
        ]
    )
    args += ["-filter_complex", ";".join(filters), "-map", "[vfinal]", "-map", "0:a?"]
    args += ["-c:v", "qtrle", "-pix_fmt", "argb"]
    if force_stereo:
        args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    else:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", str(output)]

    if not animated:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            output.unlink(missing_ok=True)
            detail = (completed.stderr or "FFmpeg produced no alpha-safe masked derivative").strip().splitlines()[-1][:500]
            raise EditorRenderError(f"Video mask pre-render failed: {detail}")
        return output.relative_to(self.project_dir).as_posix()

    frame_count = max(1, int(math.ceil(duration * mask_fps - 1e-9)))
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stderr_bytes = b""
    broken_pipe = False
    deadline = time.monotonic() + self.timeout_seconds
    try:
        if process.stdin is None:
            raise EditorRenderError("FFmpeg mask stream is unavailable")
        for frame_index in range(frame_count):
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(args, self.timeout_seconds)
            derivative_time = frame_index / mask_fps
            alpha = _mask_alpha_frame(item, (width, height), derivative_time)
            try:
                process.stdin.write(alpha.tobytes())
            except BrokenPipeError:
                broken_pipe = True
                break
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            broken_pipe = True
        process.stdin = None
        remaining = max(0.1, deadline - time.monotonic())
        process.wait(timeout=remaining)
        if process.stderr is not None:
            stderr_bytes = process.stderr.read()
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        output.unlink(missing_ok=True)
        raise EditorRenderError("Keyframed video mask pre-render timed out") from exc
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.stderr is not None and not stderr_bytes:
            try:
                stderr_bytes = process.stderr.read()
            except OSError:
                pass

    if broken_pipe or process.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        detail = stderr_bytes.decode("utf-8", "replace").strip().splitlines()
        message = detail[-1][:500] if detail else "FFmpeg produced no keyframed-mask derivative"
        raise EditorRenderError(f"Keyframed video mask pre-render failed: {message}")
    return output.relative_to(self.project_dir).as_posix()


setattr(
    _derive_video_with_keyframed_masks,
    "_aura_original_keyframed_mask_derive_video",
    _ORIGINAL_DERIVE_VIDEO,
)


def install_keyframed_mask_runtime() -> None:
    """Install authored mask animation at the mature grouped-compositor mask stage."""

    _effects.VideoItemEffectsCompositor._derive_video = _derive_video_with_keyframed_masks
    _grouped.GroupedUnifiedAdvancedVideoCompositor._validate_effect_state = (
        _validate_grouped_state_with_keyframed_masks
    )


install_keyframed_mask_runtime()


def _count_keyframed_masks(state: dict[str, Any], sequence_id: str) -> int:
    branch = state.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences") or []}
    tracks = {row.get("id"): row for row in branch.get("tracks") or []}
    items = {row.get("id"): row for row in branch.get("items") or []}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return 0
    count = 0
    for track_id in sequence.get("track_ids") or []:
        track = tracks.get(track_id)
        if not track or not track.get("enabled", True):
            continue
        for item_id in track.get("item_ids") or []:
            item = items.get(item_id)
            if not item or not item.get("enabled", True):
                continue
            count += sum(
                1
                for mask in item.get("masks") or []
                if mask.get("enabled", True) and any((mask.get("keyframes") or {}).values())
            )
    return count


class KeyframedMaskUniversalVisualVideoCompositor(ChromaKeyUniversalVisualVideoCompositor):
    """Production Video Studio compositor with authored keyframed roto-mask execution."""

    def render_video_advanced(self, sequence_id: str) -> EditorExportResult:
        state = self.store.public_state()
        keyframed_count = _count_keyframed_masks(state, sequence_id)
        result = super().render_video_advanced(sequence_id)
        metadata_path = self.project_dir / result.metadata_ref
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "professional_keyframed_mask_compositor": keyframed_count > 0,
                "professional_keyframed_mask_instances_executed": keyframed_count,
                "professional_keyframed_mask_paths_supported": sorted(SUPPORTED_KEYFRAMED_MASK_PATHS),
                "professional_keyframed_mask_shapes_supported": sorted(_grouped._SUPPORTED_STATIC_MASK_SHAPES),
                "professional_keyframed_mask_sequence_time_alignment": True,
                "professional_keyframed_mask_reverse_speed_alignment": True,
                "professional_keyframed_mask_streamed_raw_gray_frames": True,
                "professional_keyframed_mask_alpha_multiplies_existing_matte": True,
                "professional_keyframed_mask_source_media_mutated": False,
                "keyframed_masks_fail_closed": False,
                "tracked_masks_fail_closed": True,
                "tracked_or_keyframed_masks_fail_closed": False,
                "automatic_mask_tracking_supported": False,
                "mask_tracking_claimed": False,
            }
        )
        metadata_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if keyframed_count:
            return result.model_copy(update={"renderer": "ffmpeg-universal-keyframed-mask-video-compositor"})
        return result


UniversalVisualVideoCompositor = KeyframedMaskUniversalVisualVideoCompositor


__all__ = [
    "SUPPORTED_KEYFRAMED_MASK_PATHS",
    "KeyframedMaskUniversalVisualVideoCompositor",
    "UniversalVisualVideoCompositor",
    "install_keyframed_mask_runtime",
    "_mask_state_at_time",
    "_sequence_time_for_mask_frame",
]
