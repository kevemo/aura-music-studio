import math

from aura_music_studio.daw_mixer_ui import JAVASCRIPT
from aura_music_studio.session import AutomationLane, AutomationPoint, StudioSession


def test_volume_automation_is_canonical_clamped_sorted_and_deduplicated():
    lane = AutomationLane(
        parameter=" Fader ",
        points=[
            AutomationPoint(time=5.0, value=99.0),
            AutomationPoint(time=-2.0, value=-90.0),
            AutomationPoint(time=5.0, value=-3.5),
            AutomationPoint(time=float("nan"), value=0.0),
            AutomationPoint(time=8.0, value=float("inf")),
        ],
    )
    assert lane.parameter == "volume_db"
    assert [(p.time, p.value) for p in lane.points] == [(0.0, -60.0), (5.0, -3.5)]


def test_pan_automation_revalidates_on_assignment():
    lane = AutomationLane(parameter="balance", points=[])
    assert lane.parameter == "pan"
    lane.points = [
        AutomationPoint(time=3.0, value=2.0),
        AutomationPoint(time=1.0, value=-2.0),
        AutomationPoint(time=3.0, value=0.25),
    ]
    assert [(p.time, p.value) for p in lane.points] == [(1.0, -1.0), (3.0, 0.25)]


def test_unknown_future_automation_parameter_keeps_finite_values_only():
    lane = AutomationLane(
        parameter="custom_future_control",
        points=[
            AutomationPoint(time=2.0, value=120.0),
            AutomationPoint(time=float("inf"), value=4.0),
        ],
    )
    assert lane.parameter == "custom_future_control"
    assert len(lane.points) == 1
    assert math.isfinite(lane.points[0].time)
    assert lane.points[0].value == 120.0


def test_automation_survives_session_round_trip_in_canonical_form(tmp_path):
    session = StudioSession(name="mixer")
    session.add_track("Master", "master")
    vocal = session.add_track("Lead Vocal", "vocals")
    vocal.automation.append(AutomationLane(
        parameter="volume",
        points=[AutomationPoint(time=0.0, value=30.0), AutomationPoint(time=4.0, value=-6.0)],
    ))
    path = tmp_path / "aura_session.json"
    session.save(path)
    restored = StudioSession.load(path)
    lane = restored.find_track(vocal.id).automation[0]
    assert lane.parameter == "volume_db"
    assert lane.points[0].value == 18.0
    assert lane.points[1].value == -6.0


def test_mixer_ui_contains_graphical_automation_meter_and_keyboard_workflow():
    assert "ESP CHANNEL MIXER" in JAVASCRIPT
    assert "Graphical Automation" in JAVASCRIPT
    assert "dawMasterMeter" in JAVASCRIPT
    assert "volume_db" in JAVASCRIPT
    assert "Pan" in JAVASCRIPT
    assert "e.code==='Space'" in JAVASCRIPT
    assert "e.key==='Delete'||e.key==='Backspace'" in JAVASCRIPT
    assert "setLoopStart" in JAVASCRIPT
    assert "setLoopEnd" in JAVASCRIPT
    assert "renderMix" in JAVASCRIPT


def test_mixer_ui_writes_only_same_origin_project_routes():
    assert "credentials:'same-origin'" in JAVASCRIPT
    assert "/daw/tracks/" in JAVASCRIPT
    assert "/automation" in JAVASCRIPT
    assert "fetch('http://" not in JAVASCRIPT
    assert 'fetch("http://' not in JAVASCRIPT
