from __future__ import annotations

import pytest

from aura_music_studio.renderers import BaseRenderer


def test_base_renderer_fails_closed_instead_of_exposing_an_unfinished_stub():
    renderer = BaseRenderer()

    with pytest.raises(RuntimeError, match="non-executable renderer contract"):
        renderer.render(None, None, None)  # type: ignore[arg-type]
