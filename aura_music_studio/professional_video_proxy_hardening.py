from __future__ import annotations

from pathlib import Path

from .professional_video_proxy import ProfessionalVideoProxyService, VideoProxyError

_ORIGINAL_INIT = ProfessionalVideoProxyService.__init__
_INSTALLED = False


def _hardened_init(self: ProfessionalVideoProxyService, project_dir: Path) -> None:
    try:
        _ORIGINAL_INIT(self, project_dir)
    except (TypeError, ValueError) as exc:
        raise VideoProxyError("Video proxy timeout configuration is invalid") from exc
    project = Path(self.project_dir).resolve()
    proxy_root = Path(self.proxy_root).resolve()
    if project not in proxy_root.parents:
        raise VideoProxyError("Proxy root resolves outside the tenant project")


def install_professional_video_proxy_hardening() -> None:
    """Require the resolved proxy root to remain inside the tenant project.

    The underlying service already validates each item directory, temporary output and media
    resolution. This additional constructor boundary closes the remaining case where a pre-existing
    ``work`` or ``editor_proxies`` symlink could otherwise make the service's root itself external.
    Invalid operator timeout configuration also fails closed behind a bounded public error.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    ProfessionalVideoProxyService.__init__ = _hardened_init
    _INSTALLED = True


__all__ = ["install_professional_video_proxy_hardening"]
