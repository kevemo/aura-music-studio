from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass
class SongSection:
    name: str
    start_seconds: float
    end_seconds: float
    energy: float = 0.5
    lyric_excerpt: str = ""


@dataclass
class VideoShot:
    index: int
    section: str
    start_seconds: float
    end_seconds: float
    prompt: str
    camera: str
    transition: str
    lyric_excerpt: str = ""


class MusicVideoStoryboardPlanner:
    """Deterministic first-pass shot planner for Aura music-video orchestration.

    This planner intentionally produces editable shot intent rather than
    pretending to understand or render footage itself. A configured video
    provider turns each shot into real generated video.
    """

    CAMERA_BY_ENERGY = (
        (0.25, "locked cinematic composition, slow controlled movement"),
        (0.55, "medium cinematic dolly movement with gentle parallax"),
        (0.8, "dynamic tracking shot with expressive camera movement"),
        (1.01, "high-energy kinetic camera, dramatic movement and impact"),
    )

    def build(
        self,
        *,
        title: str,
        visual_concept: str,
        sections: Iterable[SongSection],
        aspect_ratio: str = "16:9",
        continuity: str = "consistent principal subject, wardrobe, lighting language, palette and location logic",
    ) -> list[VideoShot]:
        shots: list[VideoShot] = []
        for index, section in enumerate(sections, start=1):
            camera = next(label for threshold, label in self.CAMERA_BY_ENERGY if section.energy <= threshold)
            duration = max(0.1, section.end_seconds - section.start_seconds)
            prompt = (
                f"Music video for {title}. {visual_concept}. Section: {section.name}. "
                f"Maintain {continuity}. {camera}. Compose for {aspect_ratio}. "
                f"Shot duration approximately {duration:.1f} seconds."
            )
            if section.lyric_excerpt:
                prompt += f" Emotional context from lyrics: {section.lyric_excerpt[:240]}"
            shots.append(
                VideoShot(
                    index=index,
                    section=section.name,
                    start_seconds=section.start_seconds,
                    end_seconds=section.end_seconds,
                    prompt=prompt,
                    camera=camera,
                    transition="beat-aligned cut" if index > 1 else "fade in",
                    lyric_excerpt=section.lyric_excerpt,
                )
            )
        return shots

    @staticmethod
    def to_dicts(shots: Iterable[VideoShot]) -> list[dict]:
        return [asdict(shot) for shot in shots]
