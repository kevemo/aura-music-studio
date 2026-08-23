from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import librosa

from .lyrics import parse_sections
from .video_generation import VideoGenerationRequest, VideoGenerationService
from .video_storyboard import MusicVideoStoryboardPlanner, SongSection


class MusicVideoError(RuntimeError):
    pass


class MusicVideoStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_music_videos (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source_project TEXT NOT NULL,
                    title TEXT NOT NULL,
                    concept TEXT NOT NULL,
                    aspect_ratio TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    storyboard_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_music_videos_user_created
                    ON aura_music_videos(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS aura_music_video_shots (
                    id TEXT PRIMARY KEY,
                    music_video_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    render_result_id TEXT NOT NULL,
                    shot_index INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    requested_seconds INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider_job_id TEXT,
                    status TEXT NOT NULL,
                    output_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(music_video_id) REFERENCES aura_music_videos(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_music_video_shots_project
                    ON aura_music_video_shots(music_video_id, shot_index);
                """
            )
            columns = {row[1] for row in con.execute("PRAGMA table_info(aura_music_video_shots)").fetchall()}
            if "render_result_id" not in columns:
                con.execute("ALTER TABLE aura_music_video_shots ADD COLUMN render_result_id TEXT")
                con.execute("UPDATE aura_music_video_shots SET render_result_id=id WHERE render_result_id IS NULL")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_project(self, row: dict) -> None:
        now = self._now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_music_videos
                   (id,user_id,source_project,title,concept,aspect_ratio,provider,quality,audio_path,storyboard_json,status,output_path,error,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["user_id"], row["source_project"], row["title"], row["concept"],
                    row["aspect_ratio"], row["provider"], row["quality"], row["audio_path"],
                    json.dumps(row["storyboard"], ensure_ascii=False), row["status"], row.get("output_path"),
                    row.get("error"), now, now,
                ),
            )

    def save_shot(self, music_video_id: str, user_id: str, shot: dict, result: dict) -> str:
        shot_id = uuid4().hex
        now = self._now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_music_video_shots
                   (id,music_video_id,user_id,render_result_id,shot_index,section,start_seconds,end_seconds,requested_seconds,prompt,provider,model,provider_job_id,status,output_path,error,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    shot_id, music_video_id, user_id, result["id"], shot["index"], shot["section"],
                    shot["start_seconds"], shot["end_seconds"], shot["requested_seconds"], shot["prompt"],
                    result["provider"], result["model"], result.get("provider_job_id"), result["status"],
                    result.get("output_path"), result.get("error"), now, now,
                ),
            )
        return shot_id

    def list_projects(self, user_id: str, limit: int = 30) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_music_videos WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self._project_dict(row) for row in rows]

    def get_project(self, user_id: str, project_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_music_videos WHERE user_id=? AND id=?", (user_id, project_id)
            ).fetchone()
            shots = con.execute(
                "SELECT * FROM aura_music_video_shots WHERE user_id=? AND music_video_id=? ORDER BY shot_index",
                (user_id, project_id),
            ).fetchall()
        if not row:
            raise KeyError(project_id)
        project = self._project_dict(row)
        project["shots"] = [dict(x) for x in shots]
        return project

    @staticmethod
    def _project_dict(row) -> dict:
        item = dict(row)
        try:
            item["storyboard"] = json.loads(item.pop("storyboard_json"))
        except Exception:
            item["storyboard"] = []
            item.pop("storyboard_json", None)
        return item

    def update_shot(self, user_id: str, shot_id: str, refreshed: dict) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE aura_music_video_shots SET status=?,output_path=COALESCE(?,output_path),error=?,updated_at=?
                   WHERE user_id=? AND id=?""",
                (
                    refreshed.get("status") or "in_progress", refreshed.get("output_path"),
                    refreshed.get("error"), self._now(), user_id, shot_id,
                ),
            )

    def update_project(self, user_id: str, project_id: str, *, status: str, output_path: str | None = None, error: str | None = None) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE aura_music_videos SET status=?,output_path=COALESCE(?,output_path),error=?,updated_at=?
                   WHERE user_id=? AND id=?""",
                (status, output_path, error, self._now(), user_id, project_id),
            )


class AuraMusicVideoDirector:
    """Turn a completed real-audio song into a tracked, multi-shot AI music-video project."""

    def __init__(self, db_path: str | Path, output_root: str | Path | None = None):
        self.store = MusicVideoStore(db_path)
        self.video = VideoGenerationService()
        self.output_root = Path(output_root or os.getenv("AURA_MUSIC_VIDEO_OUTPUT_DIR", "outputs/music_videos"))
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.storyboards = MusicVideoStoryboardPlanner()

    @staticmethod
    def _duration(audio: Path) -> float:
        try:
            return float(librosa.get_duration(path=str(audio)))
        except Exception as exc:
            raise MusicVideoError(f"Could not read finished-song duration: {exc}") from exc

    @staticmethod
    def _section_plan(project: Path, duration: float) -> list[SongSection]:
        lyrics_path = project / "input" / "lyrics.txt"
        lyric_sections = parse_sections(lyrics_path.read_text(encoding="utf-8")) if lyrics_path.exists() else []
        if not lyric_sections:
            return [SongSection(name="Full song", start_seconds=0.0, end_seconds=duration, energy=0.65)]
        weights = [max(1, len(section.get("lines") or [])) for section in lyric_sections]
        total = sum(weights)
        cursor = 0.0
        result: list[SongSection] = []
        for index, section in enumerate(lyric_sections):
            end = duration if index == len(lyric_sections) - 1 else cursor + duration * (weights[index] / total)
            name = section.get("name") or f"Section {index+1}"
            name_lower = name.casefold()
            energy = 0.84 if "chorus" in name_lower else (0.72 if "bridge" in name_lower else (0.45 if "intro" in name_lower or "outro" in name_lower else 0.62))
            excerpt = " ".join(section.get("lines") or [])[:240]
            result.append(SongSection(name=name, start_seconds=cursor, end_seconds=end, energy=energy, lyric_excerpt=excerpt))
            cursor = end
        return result

    @staticmethod
    def _split_sections(sections: list[SongSection], max_shot: float = 8.0) -> list[SongSection]:
        result: list[SongSection] = []
        for section in sections:
            cursor = section.start_seconds
            segment = 1
            while cursor < section.end_seconds - 0.05:
                end = min(section.end_seconds, cursor + max_shot)
                result.append(
                    SongSection(
                        name=section.name if segment == 1 else f"{section.name} · shot {segment}",
                        start_seconds=cursor,
                        end_seconds=end,
                        energy=section.energy,
                        lyric_excerpt=section.lyric_excerpt,
                    )
                )
                cursor = end
                segment += 1
        return result

    @staticmethod
    def _provider_seconds(_duration: float) -> int:
        return 8

    def start(
        self,
        *,
        user_id: str,
        source_project: Path,
        title: str,
        concept: str,
        aspect_ratio: str = "16:9",
        provider: str = "auto",
        quality: str = "standard",
        continuity: str = "consistent principal subject, wardrobe, locations, lighting, color palette and cinematic visual language",
    ) -> dict:
        audio = source_project / "output" / "Aura_Final_Master.wav"
        if not audio.is_file():
            raise MusicVideoError("The source project has no completed Aura_Final_Master.wav")
        duration = self._duration(audio)
        if duration < 2:
            raise MusicVideoError("The completed song is too short for a music video")
        sections = self._split_sections(self._section_plan(source_project, duration), max_shot=8.0)
        max_shots = int(os.getenv("AURA_MUSIC_VIDEO_MAX_SHOTS", "60"))
        if len(sections) > max_shots:
            raise MusicVideoError(f"Music-video plan requires {len(sections)} shots, above the configured limit of {max_shots}")
        raw_shots = self.storyboards.build(
            title=title,
            visual_concept=concept,
            sections=sections,
            aspect_ratio=aspect_ratio,
            continuity=continuity,
        )
        storyboard = []
        for shot in raw_shots:
            item = asdict(shot)
            item["requested_seconds"] = self._provider_seconds(shot.end_seconds - shot.start_seconds)
            storyboard.append(item)

        project_id = uuid4().hex
        project_row = {
            "id": project_id,
            "user_id": user_id,
            "source_project": source_project.name,
            "title": title,
            "concept": concept,
            "aspect_ratio": aspect_ratio,
            "provider": provider,
            "quality": quality,
            "audio_path": str(audio),
            "storyboard": storyboard,
            "status": "submitting",
        }
        self.store.save_project(project_row)

        try:
            for shot in storyboard:
                result = self.video.generate(
                    VideoGenerationRequest(
                        prompt=shot["prompt"],
                        mode="text_to_video",
                        aspect_ratio=aspect_ratio,
                        duration_seconds=shot["requested_seconds"],
                        provider=provider,
                        quality=quality,
                        project_id=project_id,
                        target_platform="music_video",
                    )
                )
                self.store.save_shot(project_id, user_id, shot, result.to_dict())
            self.store.update_project(user_id, project_id, status="rendering")
        except Exception as exc:
            self.store.update_project(user_id, project_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        return self.refresh(user_id=user_id, project_id=project_id)

    def refresh(self, *, user_id: str, project_id: str) -> dict:
        try:
            project = self.store.get_project(user_id, project_id)
        except KeyError as exc:
            raise MusicVideoError("Music-video project not found") from exc
        if project["status"] == "completed" and project.get("output_path"):
            return project
        if project["status"] == "failed":
            return project

        for shot in project["shots"]:
            if shot["status"] == "completed" and shot.get("output_path"):
                continue
            if shot["status"] in {"failed", "cancelled"}:
                self.store.update_project(user_id, project_id, status="failed", error=shot.get("error") or "A music-video shot failed")
                return self.store.get_project(user_id, project_id)
            try:
                refreshed = self.video.refresh(
                    result_id=shot["render_result_id"],
                    provider=shot["provider"],
                    provider_job_id=shot.get("provider_job_id"),
                )
            except Exception as exc:
                refreshed = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            self.store.update_shot(user_id, shot["id"], refreshed)

        project = self.store.get_project(user_id, project_id)
        statuses = {shot["status"] for shot in project["shots"]}
        if statuses & {"failed", "cancelled"}:
            self.store.update_project(user_id, project_id, status="failed", error="One or more generated video shots failed")
        elif project["shots"] and all(shot["status"] == "completed" and shot.get("output_path") for shot in project["shots"]):
            try:
                output = self._assemble(project)
                self.store.update_project(user_id, project_id, status="completed", output_path=str(output))
            except Exception as exc:
                self.store.update_project(user_id, project_id, status="failed", error=f"Assembly failed: {type(exc).__name__}: {exc}")
        else:
            self.store.update_project(user_id, project_id, status="rendering")
        return self.store.get_project(user_id, project_id)

    @staticmethod
    def _size(ratio: str) -> tuple[int, int]:
        return {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}[ratio]

    def _assemble(self, project: dict) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise MusicVideoError("ffmpeg is required to assemble music-video shots")
        root = self.output_root / project["id"]
        normalized = root / "normalized"
        normalized.mkdir(parents=True, exist_ok=True)
        width, height = self._size(project["aspect_ratio"])
        concat_lines: list[str] = []
        for shot in project["shots"]:
            source = Path(shot["output_path"])
            if not source.is_file():
                raise MusicVideoError(f"Generated shot is missing: {shot['shot_index']}")
            target = normalized / f"shot_{shot['shot_index']:04d}.mp4"
            actual_duration = max(0.1, float(shot["end_seconds"]) - float(shot["start_seconds"]))
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-t", f"{actual_duration:.3f}",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
                "-an", "-c:v", "libx264", "-preset", os.getenv("AURA_VIDEO_FFMPEG_PRESET", "medium"),
                "-crf", "18", "-movflags", "+faststart", str(target),
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if completed.returncode != 0 or not target.is_file():
                raise MusicVideoError(completed.stderr[-1600:] or f"Could not normalize shot {shot['shot_index']}")
            concat_lines.append("file '" + str(target.resolve()).replace("'", "'\\''") + "'")

        manifest = root / "concat.txt"
        manifest.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        silent = root / "picture_track.mp4"
        concat = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(silent)],
            capture_output=True, text=True, check=False,
        )
        if concat.returncode != 0 or not silent.is_file():
            raise MusicVideoError(concat.stderr[-1600:] or "Could not concatenate generated shots")

        audio = Path(project["audio_path"])
        output = root / "Aura_Music_Video.mp4"
        mux = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
                "-shortest", "-movflags", "+faststart", str(output),
            ],
            capture_output=True, text=True, check=False,
        )
        if mux.returncode != 0 or not output.is_file() or output.stat().st_size < 4096:
            raise MusicVideoError(mux.stderr[-1600:] or "Could not mux original master audio into final music video")
        return output
