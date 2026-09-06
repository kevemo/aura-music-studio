from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_video_proxy import ProfessionalVideoProxyService, VideoProxyError
from aura_music_studio.professional_video_proxy_hardening import install_professional_video_proxy_hardening


def test_proxy_root_symlink_escape_is_rejected(tmp_path: Path):
    project = (tmp_path / "project").resolve()
    project.mkdir()
    ProfessionalEditorStore(project).initialize("proxy-security")

    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    work = project / "work"
    try:
        work.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this runner")

    install_professional_video_proxy_hardening()
    with pytest.raises(VideoProxyError, match="outside the tenant project"):
        ProfessionalVideoProxyService(project)


def test_invalid_proxy_timeout_configuration_fails_closed(tmp_path: Path, monkeypatch):
    project = (tmp_path / "project-timeout").resolve()
    project.mkdir()
    ProfessionalEditorStore(project).initialize("proxy-timeout-security")
    monkeypatch.setenv("AURA_EDITOR_PROXY_TIMEOUT_SECONDS", "not-a-number")

    install_professional_video_proxy_hardening()
    with pytest.raises(VideoProxyError, match="timeout configuration is invalid"):
        ProfessionalVideoProxyService(project)


def test_proxy_hardening_installer_is_idempotent():
    install_professional_video_proxy_hardening()
    first = ProfessionalVideoProxyService.__init__
    install_professional_video_proxy_hardening()
    assert ProfessionalVideoProxyService.__init__ is first
