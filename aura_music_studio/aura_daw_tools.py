from __future__ import annotations

from pathlib import Path
from typing import Any

from . import aura_agent_tools as tools
from .daw import (
    load_session,
    move_clip,
    public_session,
    save_session,
    set_automation,
    set_clip_fades,
    set_clip_gain,
    set_loop,
    set_track_mix,
)
from .mixer import render_session
from .plans import AUTOMATION, BASIC_TIMELINE, DEEP_REVISION_HISTORY, REVISION_HISTORY
from .revisions import create_revision

_INSTALLED = False


def _spec(name: str, description: str, arguments: dict[str, str], *, write: bool = False):
    return tools.ToolSpec(name=name, description=description, arguments=arguments, write=write, web=False)


DAW_SPECS = [
    _spec(
        "inspect_daw",
        "Read the pinned project's browser-safe DAW session: tracks, clips, mix state, automation, sends, markers and loop region.",
        {"project_name": "Project name/slug; omit when a project is pinned."},
    ),
    _spec(
        "set_daw_track_mix",
        "Non-destructively change one DAW track volume, pan, mute or solo. Track can be selected by id, exact/partial name or role. Volume may be absolute or relative.",
        {
            "project_name": "Project name/slug.",
            "track": "Track id, track name or role such as vocals/guitar/drums.",
            "volume_db": "Optional absolute volume in dB (-60 to +18).",
            "volume_delta_db": "Optional relative dB change, e.g. -3 means turn down 3 dB.",
            "pan": "Optional pan -1 left to +1 right.",
            "mute": "Optional boolean.",
            "solo": "Optional boolean.",
        },
        write=True,
    ),
    _spec(
        "set_daw_clip_gain",
        "Non-destructively set or adjust gain for one DAW audio clip.",
        {
            "project_name": "Project name/slug.",
            "clip": "Clip id or exact/partial clip name.",
            "gain_db": "Optional absolute clip gain (-60 to +24 dB).",
            "gain_delta_db": "Optional relative gain change.",
        },
        write=True,
    ),
    _spec(
        "set_daw_clip_fades",
        "Set non-destructive fade-in/fade-out lengths on one DAW clip.",
        {
            "project_name": "Project name/slug.",
            "clip": "Clip id or exact/partial name.",
            "fade_in": "Fade-in seconds.",
            "fade_out": "Fade-out seconds.",
        },
        write=True,
    ),
    _spec(
        "move_daw_clip",
        "Move one DAW clip to a new timeline start without changing its source audio.",
        {"project_name": "Project name/slug.", "clip": "Clip id or name.", "start": "New timeline start in seconds."},
        write=True,
    ),
    _spec(
        "set_daw_loop",
        "Set or clear the DAW loop region.",
        {"project_name": "Project name/slug.", "start": "Loop start seconds or null to clear.", "end": "Loop end seconds or null to clear."},
        write=True,
    ),
    _spec(
        "set_daw_automation",
        "Replace one Pro DAW automation lane with explicit points.",
        {
            "project_name": "Project name/slug.",
            "track": "Track id/name/role.",
            "parameter": "Automation parameter such as volume_db or pan.",
            "points": "List of {time,value} points.",
        },
        write=True,
    ),
    _spec(
        "render_daw_mix",
        "Render the current DAW session to a real-audio WAV output without changing the editable session.",
        {"project_name": "Project name/slug."},
        write=True,
    ),
]


def _project(registry, args: dict) -> tuple[str, Path]:
    name = tools._project_name(args, registry.pinned_project)
    return name, tools._safe_project(name)


def _session(registry, args: dict):
    name, project = _project(registry, args)
    try:
        session = load_session(project)
    except FileNotFoundError as exc:
        raise ValueError("This project does not have a DAW session yet") from exc
    return name, project, session


def _require_timeline(registry):
    if not registry.member.plan.has(BASIC_TIMELINE):
        raise PermissionError("DAW timeline controls require the Base or Pro membership tier")


def _snapshot(registry, project: Path, label: str):
    if not registry.member.plan.has(REVISION_HISTORY):
        return None
    keep = 200 if registry.member.plan.has(DEEP_REVISION_HISTORY) else 40
    try:
        return create_revision(project, label=label, reason="aura_chat_daw_edit", actor="Aura Core", keep=keep)
    except Exception:
        return None


def _track(session, selector: str):
    clean = (selector or "").strip().lower()
    if not clean:
        raise ValueError("Track selector is required")
    for item in session.tracks:
        if item.id.lower() == clean:
            return item
    exact = [item for item in session.tracks if item.name.strip().lower() == clean or item.role.strip().lower() == clean]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in session.tracks if clean in item.name.lower() or clean in item.role.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise KeyError(f"No DAW track matches {selector!r}")
    raise ValueError(f"Track selector {selector!r} is ambiguous; matches: {', '.join(item.name for item in partial[:8])}")


def _clip(session, selector: str):
    clean = (selector or "").strip().lower()
    if not clean:
        raise ValueError("Clip selector is required")
    all_clips = [(track, clip) for track in session.tracks for clip in track.clips]
    for track, clip in all_clips:
        if clip.id.lower() == clean:
            return track, clip
    exact = [(track, clip) for track, clip in all_clips if clip.name.strip().lower() == clean]
    if len(exact) == 1:
        return exact[0]
    partial = [(track, clip) for track, clip in all_clips if clean in clip.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise KeyError(f"No DAW clip matches {selector!r}")
    raise ValueError(f"Clip selector {selector!r} is ambiguous; matches: {', '.join(clip.name for _, clip in partial[:8])}")


def _explicit(tool_name: str, text: str) -> bool:
    lower = (text or "").lower()
    if tool_name == "inspect_daw":
        return True
    if tool_name == "render_daw_mix":
        return any(x in lower for x in ("render", "bounce", "export", "make a mix", "create a mix"))
    if tool_name == "set_daw_track_mix":
        action = any(x in lower for x in ("turn", "set", "change", "adjust", "lower", "raise", "increase", "decrease", "mute", "unmute", "solo", "pan"))
        return action
    if tool_name in {"set_daw_clip_gain", "set_daw_clip_fades", "move_daw_clip"}:
        return any(x in lower for x in ("set", "change", "adjust", "move", "gain", "fade", "turn", "increase", "decrease"))
    if tool_name == "set_daw_loop":
        return "loop" in lower
    if tool_name == "set_daw_automation":
        return "automation" in lower or "automate" in lower
    return False


def _track_payload(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "role": item.role,
        "volume_db": item.volume_db,
        "pan": item.pan,
        "mute": item.mute,
        "solo": item.solo,
    }


def install_aura_daw_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    known = {item.name for item in tools.TOOL_SPECS}
    for spec in DAW_SPECS:
        if spec.name not in known:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec

    original_execute = tools.AuraToolRegistry.execute
    names = {item.name for item in DAW_SPECS}

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name not in names:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        _require_timeline(self)
        spec = tools._SPEC_BY_NAME[call.name]
        if spec.write and not _explicit(call.name, latest_user_message):
            raise PermissionError(f"Aura did not execute {call.name}: the latest member message did not explicitly authorize this DAW change")
        args = dict(call.arguments or {})
        name, project, session = _session(self, args)

        if call.name == "inspect_daw":
            return {"project_name": name, "session": public_session(session)}

        if call.name == "set_daw_track_mix":
            target = _track(session, str(args.get("track") or ""))
            volume = args.get("volume_db")
            delta = args.get("volume_delta_db")
            if volume is None and delta is not None:
                volume = target.volume_db + float(delta)
            if all(args.get(key) is None for key in ("volume_db", "volume_delta_db", "pan", "mute", "solo")):
                raise ValueError("No track mix change was supplied")
            revision = _snapshot(self, project, f"Before Aura mixer edit — {target.name}")
            updated = set_track_mix(
                session,
                target.id,
                volume_db=volume,
                pan=args.get("pan"),
                mute=args.get("mute"),
                solo=args.get("solo"),
            )
            save_session(project, session)
            return {"project_name": name, "track": _track_payload(updated), "revision_snapshot": revision}

        if call.name == "set_daw_clip_gain":
            _, target = _clip(session, str(args.get("clip") or ""))
            gain = args.get("gain_db")
            if gain is None and args.get("gain_delta_db") is not None:
                gain = target.gain_db + float(args["gain_delta_db"])
            if gain is None:
                raise ValueError("Specify gain_db or gain_delta_db")
            revision = _snapshot(self, project, f"Before Aura clip gain — {target.name}")
            updated = set_clip_gain(session, target.id, float(gain))
            save_session(project, session)
            return {"project_name": name, "clip": {"id": updated.id, "name": updated.name, "gain_db": updated.gain_db}, "revision_snapshot": revision}

        if call.name == "set_daw_clip_fades":
            _, target = _clip(session, str(args.get("clip") or ""))
            revision = _snapshot(self, project, f"Before Aura clip fades — {target.name}")
            updated = set_clip_fades(session, target.id, float(args.get("fade_in") or 0.0), float(args.get("fade_out") or 0.0))
            save_session(project, session)
            return {"project_name": name, "clip": {"id": updated.id, "name": updated.name, "fade_in": updated.fade_in, "fade_out": updated.fade_out}, "revision_snapshot": revision}

        if call.name == "move_daw_clip":
            _, target = _clip(session, str(args.get("clip") or ""))
            if args.get("start") is None:
                raise ValueError("New clip start time is required")
            revision = _snapshot(self, project, f"Before Aura clip move — {target.name}")
            updated = move_clip(session, target.id, float(args["start"]))
            save_session(project, session)
            return {"project_name": name, "clip": {"id": updated.id, "name": updated.name, "start": updated.start}, "revision_snapshot": revision}

        if call.name == "set_daw_loop":
            start = args.get("start")
            end = args.get("end")
            revision = _snapshot(self, project, "Before Aura loop-region edit")
            set_loop(session, None if start is None else float(start), None if end is None else float(end))
            save_session(project, session)
            return {"project_name": name, "loop_start": session.loop_start, "loop_end": session.loop_end, "revision_snapshot": revision}

        if call.name == "set_daw_automation":
            if not self.member.plan.has(AUTOMATION):
                raise PermissionError("Drawn DAW automation requires the Pro membership tier")
            target = _track(session, str(args.get("track") or ""))
            parameter = str(args.get("parameter") or "").strip()
            points = args.get("points") or []
            revision = _snapshot(self, project, f"Before Aura automation — {target.name} {parameter}")
            lane = set_automation(session, target.id, parameter, points)
            save_session(project, session)
            return {"project_name": name, "track": _track_payload(target), "automation": lane.model_dump(mode="json"), "revision_snapshot": revision}

        if call.name == "render_daw_mix":
            safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session.name).strip("._") or "Aura_Session"
            output = project / "output" / "daw" / f"{safe_name}_Aura_Chat_Mix.wav"
            render_session(session, project, output, project / "work" / "daw_mix")
            relative = output.relative_to(project / "output").as_posix()
            return {"project_name": name, "rendered": True, "output": relative, "real_audio_only": True, "storage_path_exposed": False}

        raise ValueError(f"Unsupported Aura DAW tool: {call.name}")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_daw_tools"]
