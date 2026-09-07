from __future__ import annotations

from pathlib import Path

import aura_music_studio.api as api
from aura_music_studio.creation import CreateSongRequest


def test_song_creation_response_never_exposes_absolute_project_path(monkeypatch, tmp_path: Path):
    private_root = (tmp_path / "members" / "user-123").resolve()
    project = private_root / "private-song"

    def fake_build_song_project(request: CreateSongRequest, root: Path) -> Path:
        assert request.title == "Private Song"
        assert root == private_root
        return project

    monkeypatch.setattr(api, "projects_root", lambda: private_root)
    monkeypatch.setattr(api, "build_song_project", fake_build_song_project)

    result = api.create_song(CreateSongRequest(title="Private Song"))

    assert result == {"project": "private-song", "path": "private-song"}
    assert not Path(result["path"]).is_absolute()
    assert str(private_root) not in str(result)
