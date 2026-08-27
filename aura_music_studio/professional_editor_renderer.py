from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pydantic import BaseModel

from .professional_editor import ProfessionalEditorStore

ExportFormat = Literal["png", "webp", "jpeg", "mp4"]
_SAFE_EXPORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SAFE_HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})(?:[0-9A-Fa-f]{2})?$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EditorRenderError(RuntimeError):
    pass


class EditorRenderUnsupported(EditorRenderError):
    pass


class EditorExportResult(BaseModel):
    sequence_id: str
    branch_id: str
    kind: Literal["image", "video"]
    format: ExportFormat
    filename: str
    output_ref: str
    metadata_ref: str
    bytes: int
    sha256: str
    source_media_mutated: bool = False
    renderer: str
    created_at: str


class ProfessionalEditorRenderer:
    """Render a ProfessionalEditorStore graph without ever rewriting source media.

    Image sequences are composited with Pillow. Video sequences are rendered with FFmpeg
    using argv-only subprocess execution (never a shell). Every source and output path is
    confined to the current member project. Unsupported advanced graph state fails closed
    instead of silently producing an inaccurate export.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir).resolve()
        self.store = ProfessionalEditorStore(self.project_dir)
        self.output_dir = (self.project_dir / "output" / "editor").resolve()
        self.max_pixels = max(64 * 64, int(os.getenv("AURA_EDITOR_MAX_PIXELS", str(3840 * 2160))))
        self.max_video_seconds = max(1.0, float(os.getenv("AURA_EDITOR_MAX_VIDEO_SECONDS", "1800")))
        self.timeout_seconds = max(30.0, float(os.getenv("AURA_EDITOR_RENDER_TIMEOUT_SECONDS", "3600")))

    def _source(self, source_ref: str | None) -> Path:
        clean = str(source_ref or "").strip()
        if not clean:
            raise EditorRenderError("Editor item has no source media reference")
        relative = Path(clean)
        if relative.is_absolute():
            raise EditorRenderError("Editor source media must be project-relative")
        target = (self.project_dir / relative).resolve()
        if self.project_dir not in target.parents or not target.is_file():
            raise EditorRenderError("Editor source media is missing or resolves outside the project")
        return target

    def _output(self, filename: str) -> Path:
        if not _SAFE_EXPORT.fullmatch(filename) or Path(filename).name != filename:
            raise EditorRenderError("Unsafe editor export filename")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = (self.output_dir / filename).resolve()
        if self.output_dir not in target.parents:
            raise EditorRenderError("Editor export resolves outside the project output directory")
        return target

    def resolve_export(self, filename: str) -> Path:
        target = self._output(filename)
        if not target.is_file():
            raise FileNotFoundError(filename)
        return target

    @staticmethod
    def _branch_maps(state: dict[str, Any]) -> tuple[dict, dict, dict]:
        branch = state["branch"]
        sequences = {value["id"]: value for value in branch.get("sequences", [])}
        tracks = {value["id"]: value for value in branch.get("tracks", [])}
        items = {value["id"]: value for value in branch.get("items", [])}
        return sequences, tracks, items

    @staticmethod
    def advanced_state(state: dict[str, Any], sequence_id: str) -> dict[str, Any]:
        sequences, tracks, items = ProfessionalEditorRenderer._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        reasons: list[str] = []
        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track:
                continue
            if track.get("effects"):
                reasons.append(f"track:{track_id}:effects")
            if track.get("keyframes"):
                reasons.append(f"track:{track_id}:keyframes")
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item:
                    continue
                if item.get("effects"):
                    reasons.append(f"item:{item_id}:effects")
                if item.get("masks"):
                    reasons.append(f"item:{item_id}:masks")
                if item.get("keyframes"):
                    reasons.append(f"item:{item_id}:keyframes")
        return {"advanced": bool(reasons), "reasons": reasons}

    def _validate_sequence(self, sequence: dict[str, Any]) -> None:
        width = int(sequence["width"])
        height = int(sequence["height"])
        if width * height > self.max_pixels:
            raise EditorRenderError("Sequence exceeds the configured editor render pixel limit")
        if sequence["kind"] == "video" and float(sequence["duration"]) > self.max_video_seconds:
            raise EditorRenderError("Sequence exceeds the configured editor video duration limit")

    @staticmethod
    def _hex_rgba(value: str) -> tuple[int, int, int, int]:
        raw = str(value or "#000000").strip().lstrip("#")
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw) + "ff"
        elif len(raw) == 6:
            raw += "ff"
        if len(raw) != 8:
            return (0, 0, 0, 255)
        try:
            return tuple(int(raw[i : i + 2], 16) for i in range(0, 8, 2))  # type: ignore[return-value]
        except ValueError:
            return (0, 0, 0, 255)

    @staticmethod
    def _ffmpeg_color(value: str) -> str:
        match = _SAFE_HEX_COLOR.fullmatch(str(value or "").strip())
        if not match:
            raise EditorRenderError("Video background must be a hexadecimal RGB/RGBA colour")
        # FFmpeg receives only a six-digit hexadecimal literal, never an arbitrary filter token.
        return "0x" + match.group(1).lower()

    @staticmethod
    def _apply_crop(image: Image.Image, crop: dict[str, Any]) -> Image.Image:
        width, height = image.size
        left = int(width * float(crop.get("left", 0.0)))
        top = int(height * float(crop.get("top", 0.0)))
        right = width - int(width * float(crop.get("right", 0.0)))
        bottom = height - int(height * float(crop.get("bottom", 0.0)))
        if right <= left or bottom <= top:
            raise EditorRenderError("Crop removes the entire source")
        return image.crop((left, top, right, bottom))

    @staticmethod
    def _apply_colour(image: Image.Image, colour: dict[str, Any]) -> Image.Image:
        exposure = float(colour.get("exposure", 0.0))
        brightness = float(colour.get("brightness", 0.0))
        contrast = float(colour.get("contrast", 1.0))
        saturation = float(colour.get("saturation", 1.0))
        gamma = max(0.05, float(colour.get("gamma", 1.0)))
        rgb = image.convert("RGBA")
        if abs(exposure) > 1e-8 or abs(brightness) > 1e-8:
            factor = max(0.0, (2.0**exposure) * (1.0 + brightness))
            rgb_part = ImageEnhance.Brightness(rgb.convert("RGB")).enhance(factor).convert("RGBA")
            rgb_part.putalpha(rgb.getchannel("A"))
            rgb = rgb_part
        if abs(contrast - 1.0) > 1e-8:
            alpha = rgb.getchannel("A")
            rgb = ImageEnhance.Contrast(rgb.convert("RGB")).enhance(max(0.0, contrast)).convert("RGBA")
            rgb.putalpha(alpha)
        if abs(saturation - 1.0) > 1e-8:
            alpha = rgb.getchannel("A")
            rgb = ImageEnhance.Color(rgb.convert("RGB")).enhance(max(0.0, saturation)).convert("RGBA")
            rgb.putalpha(alpha)
        if abs(gamma - 1.0) > 1e-8:
            alpha = rgb.getchannel("A")
            inv = 1.0 / gamma
            lut = [max(0, min(255, int(((value / 255.0) ** inv) * 255.0 + 0.5))) for value in range(256)]
            channels = [channel.point(lut) for channel in rgb.convert("RGB").split()]
            rgb = Image.merge("RGB", channels).convert("RGBA")
            rgb.putalpha(alpha)
        return rgb

    @staticmethod
    def _apply_transform(image: Image.Image, item: dict[str, Any], track: dict[str, Any]) -> tuple[Image.Image, tuple[int, int]]:
        transform = item.get("transform") or {}
        sx = max(0.01, float(transform.get("scale_x", 1.0)))
        sy = max(0.01, float(transform.get("scale_y", 1.0)))
        target = (max(1, round(image.width * sx)), max(1, round(image.height * sy)))
        if target != image.size:
            image = image.resize(target, Image.Resampling.LANCZOS)
        rotation = float(transform.get("rotation", 0.0)) % 360.0
        if abs(rotation) > 1e-8:
            image = image.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
        opacity = max(0.0, min(1.0, float(item.get("opacity", 1.0)) * float(track.get("opacity", 1.0))))
        if opacity < 1.0:
            image.putalpha(image.getchannel("A").point(lambda value: int(value * opacity)))
        x = int(round(float(transform.get("x", 0.0))))
        y = int(round(float(transform.get("y", 0.0))))
        return image, (x, y)

    def _render_text_layer(self, item: dict[str, Any]) -> Image.Image:
        text = item.get("text") or {}
        content = str(text.get("content") or item.get("name") or "Text")[:10000]
        size = max(8, min(512, int(text.get("size") or 64)))
        fill = self._hex_rgba(str(text.get("color") or "#ffffffff"))
        font = ImageFont.load_default(size=size)
        canvas = Image.new("RGBA", (max(1, len(content) * size), size * 3), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.text((0, 0), content, fill=fill, font=font, stroke_width=max(0, int(text.get("stroke_width") or 0)))
        bbox = canvas.getbbox()
        return canvas.crop(bbox) if bbox else Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    def render_image(self, sequence_id: str, *, format: Literal["png", "webp", "jpeg"] = "png", quality: int = 92) -> EditorExportResult:
        state = self.store.public_state()
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence["kind"] != "image":
            raise EditorRenderError("Image renderer requires an image sequence")
        self._validate_sequence(sequence)
        advanced = self.advanced_state(state, sequence_id)
        if advanced["advanced"]:
            raise EditorRenderUnsupported("Masks, effects and keyframes require the advanced compositor and are not silently flattened")

        canvas = Image.new("RGBA", (int(sequence["width"]), int(sequence["height"])), self._hex_rgba(sequence.get("background")))
        source_refs: list[str] = []
        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True) or not track.get("visible", True):
                continue
            if track.get("blend_mode", "normal") != "normal":
                raise EditorRenderUnsupported("Non-normal track blend modes are not yet render-safe")
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True) or not item.get("visible", True):
                    continue
                if item.get("blend_mode", "normal") != "normal":
                    raise EditorRenderUnsupported("Non-normal item blend modes are not yet render-safe")
                if item["kind"] == "text":
                    layer = self._render_text_layer(item)
                elif item["kind"] == "image_layer":
                    source = self._source(item.get("source_ref"))
                    if source.suffix.lower() not in _IMAGE_SUFFIXES:
                        raise EditorRenderUnsupported("Image sequence contains a non-image source")
                    source_refs.append(str(source.relative_to(self.project_dir)))
                    with Image.open(source) as opened:
                        layer = opened.convert("RGBA")
                else:
                    continue
                layer = self._apply_crop(layer, item.get("crop") or {})
                layer = self._apply_colour(layer, item.get("color") or {})
                layer, (x_offset, y_offset) = self._apply_transform(layer, item, track)
                x = (canvas.width - layer.width) // 2 + x_offset
                y = (canvas.height - layer.height) // 2 + y_offset
                canvas.alpha_composite(layer, (x, y))

        extension = "jpg" if format == "jpeg" else format
        filename = f"{sequence_id}_{uuid4().hex[:12]}.{extension}"
        output = self._output(filename)
        temporary = output.with_name(output.name + ".part")
        save_args: dict[str, Any] = {}
        rendered = canvas
        if format == "jpeg":
            rendered = canvas.convert("RGB")
            save_args["quality"] = max(1, min(100, int(quality)))
        elif format == "webp":
            save_args["quality"] = max(1, min(100, int(quality)))
        rendered.save(temporary, format="JPEG" if format == "jpeg" else format.upper(), **save_args)
        temporary.replace(output)
        return self._record_result(sequence, state, output, format, "pillow-rgba-compositor", source_refs)

    @staticmethod
    def _ff(value: float) -> str:
        if not math.isfinite(float(value)):
            raise EditorRenderError("Non-finite editor value cannot be rendered")
        return f"{float(value):.8f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _has_audio(path: Path) -> bool:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return False
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def render_video(self, sequence_id: str) -> EditorExportResult:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EditorRenderUnsupported("FFmpeg is unavailable on this runtime")
        state = self.store.public_state()
        sequences, tracks, items = self._branch_maps(state)
        sequence = sequences.get(sequence_id)
        if sequence is None:
            raise KeyError(sequence_id)
        if sequence["kind"] != "video":
            raise EditorRenderError("Video renderer requires a video sequence")
        self._validate_sequence(sequence)
        advanced = self.advanced_state(state, sequence_id)
        if advanced["advanced"]:
            raise EditorRenderUnsupported("Masks, effects and keyframes require the advanced compositor and are not silently flattened")

        width, height = int(sequence["width"]), int(sequence["height"])
        fps, duration = float(sequence["fps"]), float(sequence["duration"])
        background = self._ffmpeg_color(str(sequence.get("background") or "#000000"))
        visual: list[tuple[dict, dict, Path]] = []
        audio: list[tuple[dict, Path]] = []
        source_refs: list[str] = []
        for track_id in sequence.get("track_ids", []):
            track = tracks.get(track_id)
            if not track or not track.get("enabled", True):
                continue
            if track.get("blend_mode", "normal") != "normal":
                raise EditorRenderUnsupported("Non-normal video track blend modes are not yet render-safe")
            for item_id in track.get("item_ids", []):
                item = items.get(item_id)
                if not item or not item.get("enabled", True):
                    continue
                if item.get("blend_mode", "normal") != "normal":
                    raise EditorRenderUnsupported("Non-normal video item blend modes are not yet render-safe")
                if item["kind"] in {"video_clip", "image_layer"} and item.get("visible", True) and track.get("visible", True):
                    source = self._source(item.get("source_ref"))
                    allowed = _VIDEO_SUFFIXES if item["kind"] == "video_clip" else _IMAGE_SUFFIXES
                    if source.suffix.lower() not in allowed:
                        raise EditorRenderUnsupported("Timeline visual item has an unsupported source type")
                    visual.append((track, item, source))
                    source_refs.append(str(source.relative_to(self.project_dir)))
                    if (
                        item["kind"] == "video_clip"
                        and not track.get("muted", False)
                        and not bool((item.get("audio") or {}).get("muted", False))
                        and self._has_audio(source)
                    ):
                        audio.append((item, source))
                elif item["kind"] == "audio_clip" and not track.get("muted", False) and not bool((item.get("audio") or {}).get("muted", False)):
                    source = self._source(item.get("source_ref"))
                    if source.suffix.lower() not in (_AUDIO_SUFFIXES | _VIDEO_SUFFIXES):
                        raise EditorRenderUnsupported("Timeline audio item has an unsupported source type")
                    audio.append((item, source))
                    source_refs.append(str(source.relative_to(self.project_dir)))
                elif item["kind"] == "text":
                    raise EditorRenderUnsupported("Video text layers require the advanced compositor and are not silently omitted")

        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:r={self._ff(fps)}:d={self._ff(duration)}"]
        input_indexes: dict[str, int] = {}
        next_index = 1
        for _track, item, source in visual:
            key = f"v:{item['id']}"
            if item["kind"] == "image_layer":
                args += ["-loop", "1", "-t", self._ff(float(item["duration"])), "-i", str(source)]
            else:
                args += ["-i", str(source)]
            input_indexes[key] = next_index
            next_index += 1
        audio_indexes: list[tuple[int, dict]] = []
        for item, source in audio:
            args += ["-i", str(source)]
            audio_indexes.append((next_index, item))
            next_index += 1

        filters: list[str] = ["[0:v]format=rgba[base0]"]
        current = "base0"
        for idx, (track, item, _source) in enumerate(visual, start=1):
            input_index = input_indexes[f"v:{item['id']}"]
            transform, crop, colour = item.get("transform") or {}, item.get("crop") or {}, item.get("color") or {}
            left, top = float(crop.get("left", 0.0)), float(crop.get("top", 0.0))
            right, bottom = float(crop.get("right", 0.0)), float(crop.get("bottom", 0.0))
            sx, sy = float(transform.get("scale_x", 1.0)), float(transform.get("scale_y", 1.0))
            start, item_duration = float(item["start"]), float(item["duration"])
            source_in = float(item.get("source_in") or 0.0)
            speed = float(item.get("speed") or 1.0)
            if item.get("reverse"):
                raise EditorRenderUnsupported("Reverse video export is not yet render-safe")
            chain = f"[{input_index}:v]"
            if item["kind"] == "video_clip":
                chain += f"trim=start={self._ff(source_in)}:duration={self._ff(item_duration * speed)},setpts=(PTS-STARTPTS)/{self._ff(speed)}+{self._ff(start)}/TB,"
            else:
                chain += f"trim=duration={self._ff(item_duration)},setpts=PTS-STARTPTS+{self._ff(start)}/TB,"
            chain += f"crop=iw*{self._ff(1-left-right)}:ih*{self._ff(1-top-bottom)}:iw*{self._ff(left)}:ih*{self._ff(top)},scale=iw*{self._ff(sx)}:ih*{self._ff(sy)},"
            brightness = float(colour.get("brightness", 0.0)) + float(colour.get("exposure", 0.0)) * 0.1
            chain += f"eq=brightness={self._ff(max(-1,min(1,brightness)))}:contrast={self._ff(max(0,float(colour.get('contrast',1.0))))}:saturation={self._ff(max(0,float(colour.get('saturation',1.0))))}:gamma={self._ff(max(.05,float(colour.get('gamma',1.0))))},format=rgba,"
            rotation = float(transform.get("rotation", 0.0)) % 360.0
            if abs(rotation) > 1e-8:
                chain += f"rotate={self._ff(rotation)}*PI/180:c=none:ow=rotw(iw):oh=roth(ih),"
            opacity = max(0.0, min(1.0, float(item.get("opacity", 1.0)) * float(track.get("opacity", 1.0))))
            chain += f"colorchannelmixer=aa={self._ff(opacity)}[layer{idx}]"
            filters.append(chain)
            x = f"(W-w)/2+{self._ff(float(transform.get('x',0.0)))}"
            y = f"(H-h)/2+{self._ff(float(transform.get('y',0.0)))}"
            out = f"base{idx}"
            filters.append(f"[{current}][layer{idx}]overlay=x='{x}':y='{y}':enable='between(t,{self._ff(start)},{self._ff(start+item_duration)})':eof_action=pass[{out}]")
            current = out
        filters.append(f"[{current}]trim=duration={self._ff(duration)},fps={self._ff(fps)},format=yuv420p[vout]")

        audio_labels: list[str] = []
        for idx, (input_index, item) in enumerate(audio_indexes):
            config = item.get("audio") or {}
            if abs(float(config.get("pan", 0.0))) > 1e-8:
                raise EditorRenderUnsupported("Audio pan automation is not yet render-safe in video export")
            source_in, item_duration = float(item.get("source_in") or 0.0), float(item["duration"])
            start = float(item["start"])
            gain = 10.0 ** (float(config.get("gain_db", 0.0)) / 20.0)
            chain = f"[{input_index}:a]atrim=start={self._ff(source_in)}:duration={self._ff(item_duration)},asetpts=PTS-STARTPTS,volume={self._ff(gain)}"
            fade_in, fade_out = float(config.get("fade_in", 0.0)), float(config.get("fade_out", 0.0))
            if fade_in > 0:
                chain += f",afade=t=in:st=0:d={self._ff(min(fade_in,item_duration))}"
            if fade_out > 0:
                chain += f",afade=t=out:st={self._ff(max(0,item_duration-fade_out))}:d={self._ff(min(fade_out,item_duration))}"
            chain += f",adelay={max(0,int(round(start*1000)))}:all=1[a{idx}]"
            filters.append(chain)
            audio_labels.append(f"[a{idx}]")
        if audio_labels:
            filters.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0,atrim=duration={self._ff(duration)}[aout]")

        filename = f"{sequence_id}_{uuid4().hex[:12]}.mp4"
        output = self._output(filename)
        temporary = output.with_name(output.stem + ".part.mp4")
        args += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
        if audio_labels:
            args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        else:
            args += ["-an"]
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart", "-t", self._ff(duration), str(temporary)]
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout_seconds, check=False)
            if completed.returncode != 0 or not temporary.is_file():
                detail = (completed.stderr or "FFmpeg produced no export").strip().splitlines()[-1][:500]
                raise EditorRenderError(f"FFmpeg editor render failed: {detail}")
            temporary.replace(output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return self._record_result(sequence, state, output, "mp4", "ffmpeg-filter-compositor", source_refs)

    def _record_result(self, sequence: dict[str, Any], state: dict[str, Any], output: Path, format: ExportFormat, renderer: str, source_refs: list[str]) -> EditorExportResult:
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        relative = str(output.relative_to(self.project_dir))
        metadata = output.with_suffix(output.suffix + ".json")
        payload = {
            "schema_version": 1,
            "sequence_id": sequence["id"],
            "branch_id": state["branch"]["id"],
            "kind": sequence["kind"],
            "format": format,
            "output_ref": relative,
            "bytes": output.stat().st_size,
            "sha256": digest,
            "renderer": renderer,
            "source_refs": sorted(set(source_refs)),
            "source_media_mutated": False,
            "created_at": _now(),
        }
        metadata.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return EditorExportResult(
            sequence_id=sequence["id"],
            branch_id=state["branch"]["id"],
            kind=sequence["kind"],
            format=format,
            filename=output.name,
            output_ref=relative,
            metadata_ref=str(metadata.relative_to(self.project_dir)),
            bytes=output.stat().st_size,
            sha256=digest,
            renderer=renderer,
            created_at=payload["created_at"],
        )


__all__ = ["EditorExportResult", "EditorRenderError", "EditorRenderUnsupported", "ProfessionalEditorRenderer"]
