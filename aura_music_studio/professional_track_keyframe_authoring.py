from __future__ import annotations

from .professional_editor import EditorKeyframe, EditorTrack, ProfessionalEditorStore


def set_track_keyframes(
    store: ProfessionalEditorStore,
    track_id: str,
    parameter: str,
    keyframes: list[dict | EditorKeyframe],
    *,
    actor: str = "Member",
) -> EditorTrack:
    """Persist track automation through the editor's normal undo/history graph.

    The editor schema has always allowed ``EditorTrack.keyframes``; this service gives the public
    editor API a safe mutation path without hand-editing ``pro_editor.json``. Parameter-path render
    safety remains the renderer's responsibility, matching ``set_item_keyframes``: unsupported
    authored state is preserved but export fails closed rather than silently dropping it.
    """

    clean_parameter = str(parameter or "").strip()
    if not clean_parameter or len(clean_parameter) > 160:
        raise ValueError("A valid keyframe parameter path is required")

    project = store.load()
    branch = store._branch(project)
    track = store._track(branch, track_id)
    if store._locked(branch, "track", track.id):
        raise PermissionError("Track is locked")

    before = store._capture(branch, [("track", track.id)])
    points = [
        value if isinstance(value, EditorKeyframe) else EditorKeyframe.model_validate(value)
        for value in keyframes
    ]
    by_time: dict[float, EditorKeyframe] = {float(value.time): value for value in points}
    track.keyframes[clean_parameter] = [by_time[key] for key in sorted(by_time)]
    store._touch(branch, track)
    after = store._capture(branch, [("track", track.id)])
    store._record(
        branch,
        operation="set_keyframes",
        label=f"Keyframe track {track.name} · {clean_parameter}",
        before=before,
        after=after,
        actor=actor,
        target_type="track",
        target_id=track.id,
        metadata={"parameter": clean_parameter, "keyframes": len(points), "target": "track"},
    )
    store.save(project)
    return track


__all__ = ["set_track_keyframes"]
