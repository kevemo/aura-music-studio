from __future__ import annotations

import subprocess
import sys
import textwrap


def test_visual_logic_workbench_is_mounted_on_release_app_and_uses_typed_api_only():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        required = "/game-creation/visual-logic/{game_id}/{entity_id}"
        paths = {getattr(route, "path", None) for route in production_entrypoint.app.router.routes}
        if required not in paths:
            raise SystemExit(f"Game Forge Visual Logic workbench route is missing: {required}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_visual_logic_workbench_source_has_no_arbitrary_code_execution_controls():
    from aura_music_studio import game_forge_visual_logic_portal as portal

    source = portal.visual_logic_portal.__code__.co_consts
    joined = "\n".join(item for item in source if isinstance(item, str))
    assert "Compile Graph" in joined
    assert "follow_target" in joined
    assert "timer" in joined
    assert "door" in joined
    assert "eval(" not in joined
    assert "new Function" not in joined
    assert "javascript source" not in joined.lower()
