from __future__ import annotations

import aura_music_studio.shared_sky_live_watch_engagement_wave6 as wave6


def test_engagement_script_cannot_be_terminated_by_broadcast_identifier():
    malicious = "live-1</script><script>globalThis.pwned=true</script>"
    script = wave6._engagement_script(malicious)

    assert "live-1</script><script>globalThis.pwned=true</script>" not in script
    assert "live-1<\\/script><script>globalThis.pwned=true<\\/script>" in script
    assert script.count("</script>") == 1
