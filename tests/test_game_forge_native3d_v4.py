from __future__ import annotations

from types import SimpleNamespace

import pytest

import aura_music_studio.game_forge_native3d_v4 as v4


FRAME_ANCHOR = "let last=performance.now();function frame(now){const dt="
CAMERA_ANCHOR = "const pp=player.position,target=[pp.x,pp.y+.7,pp.z],eye=[pp.x+Math.sin(yaw)*Math.cos(pitch)*distance,pp.y+2+Math.sin(pitch)*distance,pp.z+Math.cos(yaw)*Math.cos(pitch)*distance],aspect=resize(),vp=mul(perspective(Math.PI/3,aspect,.08,5000),lookAt(eye,target,[0,1,0]));"


def test_v4_payload_extends_v3_with_closed_declarative_cinematic_contract(monkeypatch):
    monkeypatch.setattr(
        v4,
        "_v3_runtime_payload",
        lambda _game, _world: {"runtime_version": 3, "runtime_contract": {"native_aura_renderer": True}},
    )
    monkeypatch.setattr(v4, "cinematic_runtime_payload", lambda _game_id: {"duration_s": 4, "cues": []})
    payload = v4._runtime_payload(SimpleNamespace(id="game-one"), SimpleNamespace())
    assert payload["runtime_version"] == 4
    assert payload["cinematic"]["duration_s"] == 4
    contract = payload["runtime_contract"]
    assert contract["native_aura_renderer"] is True
    assert contract["declarative_cinematics"] is True
    assert contract["cinematic_transform_tracks"] is True
    assert contract["cinematic_camera_tracks"] is True
    assert contract["cinematic_subtitles"] is True
    assert contract["built_in_particle_vfx"] is True
    assert contract["creator_javascript"] is False
    assert contract["creator_shader_code"] is False
    assert contract["skeletal_animation"] is False


def _v3_document() -> str:
    return (
        "<!doctype html><html><head><title>Aura Game Engine 3D v3</title></head><body>"
        "<div>Aura3D v3</div><script>const cfg={\"runtime_version\": 3};"
        + FRAME_ANCHOR
        + "0;"
        + CAMERA_ANCHOR
        + "requestAnimationFrame(frame)}</script></body></html>"
    )


def test_v4_renderer_migrates_version_and_hooks_frame_and_camera(monkeypatch):
    monkeypatch.setattr(v4, "_runtime_payload", lambda _game, _world: {"runtime_version": 4})
    monkeypatch.setattr(v4, "_render_v3", lambda _game, _world, *, csp: _v3_document())
    monkeypatch.setattr(v4, "_cinematic_script", lambda _game_id: "<div id='cinematic-marker'></div>")
    html = v4.render_aura3d_playtest(SimpleNamespace(id="game-one"), SimpleNamespace(), csp="default-src 'none'")
    assert "Aura3D v4" in html
    assert "Aura Game Engine 3D v4" in html
    assert '"runtime_version": 4' in html
    assert "applyAuraCinematic(now)" in html
    assert "cinematicState.camera?.target||defaultTarget" in html
    assert "id='cinematic-marker'" in html


def test_v4_refuses_silent_renderer_drift_when_frame_anchor_changes(monkeypatch):
    monkeypatch.setattr(v4, "_runtime_payload", lambda _game, _world: {"runtime_version": 4})
    monkeypatch.setattr(v4, "_render_v3", lambda _game, _world, *, csp: "<html><body>changed renderer</body></html>")
    with pytest.raises(ValueError, match="frame contract changed"):
        v4.render_aura3d_playtest(SimpleNamespace(id="game-one"), SimpleNamespace(), csp="default-src 'none'")
