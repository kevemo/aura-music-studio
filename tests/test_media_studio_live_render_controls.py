from types import SimpleNamespace

from aura_music_studio.media_studios import _page


def _request(plan_id: str = "ultimate_pro"):
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(plan=SimpleNamespace(id=plan_id)),
        )
    )


def _html(kind: str) -> str:
    return _page(kind, _request()).body.decode("utf-8")


def test_video_studio_has_live_render_monitor_and_safe_cancel_control():
    html = _html("video")

    assert "Aura Video Studio" in html
    assert "const KIND='video',POLL_MS=3000" in html
    assert "setInterval(()=>void pollActiveRenders(),POLL_MS)" in html
    assert "/cancel-render" in html
    assert "Cancel render" in html
    assert "Refresh now" in html
    assert "Live render monitor" in html
    assert "ready to import" in html
    assert "Check render" not in html


def test_image_designer_pauses_background_polling_and_resumes_when_visible():
    html = _html("image")

    assert "Aura Image Designer" in html
    assert "const KIND='image',POLL_MS=3000" in html
    assert "document.hidden" in html
    assert "visibilitychange" in html
    assert "if(!document.hidden)void pollActiveRenders(true)" in html
    assert "beforeunload" in html
    assert "stopMonitor" in html


def test_live_render_controls_prevent_overlapping_or_duplicate_actions():
    html = _html("video")

    assert "pollBusy=false" in html
    assert "directiveBusy=new Set()" in html
    assert "if(pollBusy||!manifest" in html
    assert "if(directiveBusy.has(id))return" in html
    assert "directiveBusy.add(id)" in html
    assert "directiveBusy.delete(id)" in html


def test_live_render_status_is_accessible_and_surfaces_terminal_states():
    html = _html("image")

    assert "aria-live='polite'" in html
    assert "role=\"status\"" in html
    assert "Render complete" in html
    assert "Render failed" in html
    assert "Last render cancelled safely" in html
    assert "Import outputs" in html
