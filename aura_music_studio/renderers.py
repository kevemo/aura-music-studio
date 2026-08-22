from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import requests
from gradio_client import Client, handle_file

from .acestep_api import AceStepClient, AceStepRequest
from .cloud_providers import ElevenMusicClient, MurekaClient
from .models import ArrangementPlan, ProjectManifest, RenderResult
from .project import ProjectWorkspace

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


def _first_audio(value) -> Path | None:
    values = value if isinstance(value, (list, tuple)) else [value]
    for item in values:
        candidates = []
        if isinstance(item, str):
            candidates.append(item)
        elif isinstance(item, dict):
            for key in ("path", "name", "url"):
                if item.get(key):
                    candidates.append(item[key])
        else:
            p = getattr(item, "path", None)
            if p:
                candidates.append(p)
        for candidate in candidates:
            try:
                p = Path(candidate)
                if p.exists() and p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    return p
            except Exception:
                pass
    return None


def _lyrics(workspace: ProjectWorkspace, manifest: ProjectManifest) -> str:
    path = workspace.resolve_asset(manifest.lyrics_file)
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


class BaseRenderer:
    name = "base"

    def available(self) -> bool:
        return True

    def render(self, workspace: ProjectWorkspace, manifest: ProjectManifest, plan: ArrangementPlan) -> RenderResult:
        raise NotImplementedError


class AceStepApiRenderer(BaseRenderer):
    """Direct adapter for the official ACE-Step 1.5 async REST API."""

    name = "acestep_api"

    def available(self) -> bool:
        return bool(os.getenv("AURA_ACESTEP_API_URL"))

    def render(self, workspace, manifest, plan):
        client = AceStepClient()
        source = _source_or_guide(workspace, manifest)
        duration = min(_target_duration(workspace, manifest, plan), 600)
        lyrics = _lyrics(workspace, manifest)
        task_type = "text2music"
        src_audio = None
        reference_audio = None

        if manifest.mode in {"cover", "remix", "backing_track"} and source and source.exists():
            task_type = "cover"
            src_audio = str(source)
        elif manifest.reference_audio:
            ref = workspace.resolve_asset(manifest.reference_audio)
            if ref and ref.exists():
                reference_audio = str(ref)

        request = AceStepRequest(
            prompt=plan.render_prompt,
            lyrics=lyrics,
            task_type=task_type,
            model=manifest.renderer.model,
            bpm=round(plan.tempo_bpm) if plan.tempo_bpm else None,
            key_scale=plan.key,
            time_signature=plan.meter.split("/")[0],
            audio_duration=float(duration),
            thinking=task_type in {"text2music", "lego", "complete"},
            use_format=True,
            audio_format="wav",
            inference_steps=8,
            guidance_scale=7.0,
            batch_size=1,
            audio_cover_strength=manifest.renderer.cover_strength,
            src_audio=src_audio,
            reference_audio=reference_audio,
        )
        outputs = client.generate(request, workspace.work_dir / "acestep_api")
        return RenderResult(
            renderer=self.name,
            audio_path=outputs[0],
            audio_origin="neural",
            metadata={"task_type": task_type, "api": client.base_url, "model": manifest.renderer.model},
        )


class DeapiRenderer(BaseRenderer):
    name = "deapi"

    def available(self) -> bool:
        return bool(os.getenv("DEAPI_API_KEY"))

    def render(self, workspace, manifest, plan):
        api_key = os.environ["DEAPI_API_KEY"]
        base = os.getenv("DEAPI_BASE_URL", "https://api.deapi.ai").rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        duration = _target_duration(workspace, manifest, plan)
        payload = {
            "model": os.getenv("DEAPI_MUSIC_MODEL", "AceStep_1_5_XL_Turbo_INT8"),
            "caption": plan.render_prompt,
            "lyrics": _lyrics(workspace, manifest),
            "duration": min(duration, manifest.renderer.duration_limit_seconds),
            "bpm": round(plan.tempo_bpm),
            "key": plan.key,
            "time_signature": plan.meter,
            "format": "wav",
        }
        r = requests.post(f"{base}/api/v2/audio/music", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        job = r.json()
        request_id = job.get("request_id") or job.get("id")
        if not request_id:
            raise RuntimeError(f"deAPI returned no request id: {job}")
        deadline = time.time() + int(os.getenv("DEAPI_TIMEOUT_SECONDS", "1800"))
        while time.time() < deadline:
            s = requests.get(f"{base}/api/v2/jobs/{request_id}", headers=headers, timeout=60)
            s.raise_for_status()
            data = s.json()
            state = str(data.get("status", "")).lower()
            if state in {"done", "completed", "success", "succeeded"}:
                url = data.get("result_url") or data.get("url") or data.get("output_url")
                if not url:
                    raise RuntimeError(f"deAPI completed without an audio URL: {data}")
                out = workspace.work_dir / "neural_master_deapi.wav"
                with requests.get(url, stream=True, timeout=180) as audio:
                    audio.raise_for_status()
                    with out.open("wb") as f:
                        for chunk in audio.iter_content(1024 * 1024):
                            f.write(chunk)
                return RenderResult(renderer=self.name, audio_path=out, metadata=data)
            if state in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"deAPI generation failed: {data}")
            time.sleep(8)
        raise TimeoutError("deAPI generation timed out")


class ElevenMusicRenderer(BaseRenderer):
    name = "eleven_music"

    def available(self) -> bool:
        return bool(os.getenv("ELEVENLABS_API_KEY"))

    def render(self, workspace, manifest, plan):
        client = ElevenMusicClient()
        lyrics = _lyrics(workspace, manifest)
        duration = min(_target_duration(workspace, manifest, plan), 600)
        prompt = plan.render_prompt
        if lyrics:
            prompt += "\n\nUse these user-supplied lyrics and section labels:\n" + lyrics
        out = workspace.work_dir / "neural_master_eleven_music.mp3"
        client.compose(
            out,
            prompt=prompt[:4100],
            duration_seconds=duration,
            instrumental=not bool(lyrics),
            model=os.getenv("ELEVEN_MUSIC_MODEL", "music_v2"),
            c2pa=True,
        )
        return RenderResult(renderer=self.name, audio_path=out, metadata={"model": os.getenv("ELEVEN_MUSIC_MODEL", "music_v2")})


class MurekaRenderer(BaseRenderer):
    name = "mureka"

    def available(self) -> bool:
        return bool(os.getenv("MUREKA_API_KEY"))

    def render(self, workspace, manifest, plan):
        client = MurekaClient()
        lyrics = _lyrics(workspace, manifest)
        if lyrics:
            out = workspace.work_dir / "neural_master_mureka.mp3"
            client.lyrics_to_song(
                out,
                lyrics=lyrics[:5000],
                prompt=plan.render_prompt[:1024],
                model=os.getenv("MUREKA_MODEL", "auto"),
            )
        else:
            out = workspace.work_dir / "neural_master_mureka_instrumental.mp3"
            client.instrumental(out, prompt=plan.render_prompt[:1024], model=os.getenv("MUREKA_MODEL", "auto"))
        return RenderResult(renderer=self.name, audio_path=out, metadata={"model": os.getenv("MUREKA_MODEL", "auto")})


class AceStepSpaceRenderer(BaseRenderer):
    name = "acestep_space"

    def render(self, workspace, manifest, plan):
        source = _source_or_guide(workspace, manifest)
        hosts = [x.strip() for x in os.getenv(
            "AURA_ACESTEP_SPACES",
            "critesjosh/ace-step-music-studio,ACE-Step/Ace-Step-v1.5",
        ).split(",") if x.strip()]
        errors = []
        for cycle in range(manifest.renderer.max_attempts_per_host):
            for host in hosts:
                try:
                    result = self._render_host(host, source, workspace, manifest, plan)
                    src = _first_audio(result)
                    if not src:
                        raise RuntimeError("Space returned no downloadable local audio")
                    dst = workspace.work_dir / f"neural_master_{host.replace('/', '_')}{src.suffix}"
                    shutil.copy2(src, dst)
                    return RenderResult(renderer=f"{self.name}:{host}", audio_path=dst, metadata={"host": host})
                except Exception as exc:
                    errors.append(f"{host}: {type(exc).__name__}: {exc}")
                    workspace.log(errors[-1], "renderer.log")
            if cycle + 1 < manifest.renderer.max_attempts_per_host:
                time.sleep(manifest.renderer.retry_seconds)
        raise RuntimeError("All ACE-Step Spaces failed: " + " | ".join(errors[-8:]))

    def _render_host(self, host: str, source: Path | None, workspace: ProjectWorkspace, manifest: ProjectManifest, plan: ArrangementPlan):
        duration = min(_target_duration(workspace, manifest, plan), manifest.renderer.duration_limit_seconds)
        mode = "cover" if source and source.exists() else "text2music"
        source_arg = handle_file(str(source)) if source and source.exists() else None
        lyrics = _lyrics(workspace, manifest) or "[Instrumental]"
        args = [
            manifest.renderer.model, mode, plan.render_prompt, "en", plan.render_prompt, lyrics,
            round(plan.tempo_bpm), plan.key or "", plan.meter.split("/")[0], "en",
            8, 7.0, True, "-1", None, duration, 1,
            source_arg, "", 0.0, -1,
            "Fill the audio semantic mask based on the given conditions:",
            manifest.renderer.cover_strength, mode, False, 0.0, 1.0, 3.0, "ode", "", "wav",
            0.75, False, 2.0, 0, 0.9, plan.negative_prompt,
            True, True, False, False, True, False, False, 0.5, 8, "guitar", [], False,
        ]
        if len(args) != 49:
            raise AssertionError("ACE-Step generation schema changed")
        return Client(host).predict(*args, api_name="/generation_wrapper")


class ExternalCommandRenderer(BaseRenderer):
    def __init__(self, name: str, env_var: str, output_name: str):
        self.name = name
        self.env_var = env_var
        self.output_name = output_name

    def available(self) -> bool:
        return bool(os.getenv(self.env_var))

    def render(self, workspace, manifest, plan):
        out = workspace.work_dir / self.output_name
        source = _source_or_guide(workspace, manifest)
        lyrics_path = workspace.resolve_asset(manifest.lyrics_file)
        env = os.environ.copy()
        env.update({
            "AURA_PROJECT": str(workspace.root),
            "AURA_SOURCE": str(source) if source else "",
            "AURA_GUIDE": str(workspace.work_dir / "score_guide.wav"),
            "AURA_LYRICS": str(lyrics_path) if lyrics_path and lyrics_path.exists() else "",
            "AURA_PROMPT": plan.render_prompt,
            "AURA_NEGATIVE_PROMPT": plan.negative_prompt,
            "AURA_BPM": str(plan.tempo_bpm),
            "AURA_KEY": plan.key or "",
            "AURA_METER": plan.meter,
            "AURA_DURATION": str(_target_duration(workspace, manifest, plan)),
            "AURA_OUTPUT": str(out),
        })
        subprocess.run(shlex.split(os.environ[self.env_var]), cwd=workspace.root, env=env, check=True)
        if not out.exists():
            raise RuntimeError(f"{self.name} command completed but did not create {out}")
        return RenderResult(renderer=self.name, audio_path=out)


def _source_or_guide(workspace: ProjectWorkspace, manifest: ProjectManifest) -> Path | None:
    source = workspace.resolve_asset(manifest.reference_audio)
    if source and source.exists():
        return source
    guide = workspace.work_dir / "score_guide.wav"
    return guide if guide.exists() else None


def _target_duration(workspace: ProjectWorkspace | None, manifest: ProjectManifest, plan: ArrangementPlan) -> int:
    if manifest.target_duration_seconds:
        return int(manifest.target_duration_seconds)
    if manifest.total_measures and plan.tempo_bpm:
        beats_per_bar = int(manifest.meter.split("/")[0])
        return int(round(manifest.total_measures * beats_per_bar * 60.0 / plan.tempo_bpm))
    if workspace:
        reference = workspace.resolve_asset(manifest.reference_audio)
        if reference and reference.exists():
            try:
                import soundfile as sf
                info = sf.info(reference)
                return int(round(info.frames / info.samplerate))
            except Exception:
                pass
    return min(180, manifest.renderer.duration_limit_seconds)


def render_with_failover(workspace: ProjectWorkspace, manifest: ProjectManifest, plan: ArrangementPlan) -> RenderResult:
    renderers = {
        "acestep_api": AceStepApiRenderer(),
        "deapi": DeapiRenderer(),
        "eleven_music": ElevenMusicRenderer(),
        "mureka": MurekaRenderer(),
        "acestep_space": AceStepSpaceRenderer(),
        "local_acestep": ExternalCommandRenderer("local_acestep", "AURA_LOCAL_RENDER_CMD", "neural_master_local.wav"),
        "muser": ExternalCommandRenderer("muser", "AURA_MUSER_CMD", "neural_master_muser.wav"),
        "yue": ExternalCommandRenderer("yue", "AURA_YUE_CMD", "neural_master_yue.wav"),
    }
    requested = list(manifest.renderer.preferred)
    if manifest.mode in {"cover", "remix", "backing_track"}:
        guide_first = ["acestep_api", "local_acestep", "muser", "acestep_space", "deapi", "mureka", "eleven_music", "yue"]
        requested = [x for x in guide_first if x in requested] + [x for x in requested if x not in guide_first]

    errors = []
    for name in requested:
        renderer = renderers.get(name)
        if not renderer:
            errors.append(f"Unknown renderer: {name}")
            continue
        if not renderer.available():
            errors.append(f"Renderer unavailable: {name}")
            continue
        try:
            result = renderer.render(workspace, manifest, plan)
            workspace.log(f"Renderer success: {result.renderer}", "renderer.log")
            return result
        except Exception as exc:
            msg = f"Renderer failed {name}: {type(exc).__name__}: {exc}"
            errors.append(msg)
            workspace.log(msg, "renderer.log")
    raise RuntimeError("No renderer completed successfully. " + " | ".join(errors))
