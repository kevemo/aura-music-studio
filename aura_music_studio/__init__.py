"""Elevate Souls Productions Content Creation Command Center — Powered by Aura AI.

Elevate Your Soul Through Purposeful Media.

The ``aura_music_studio`` package identifier remains for backwards compatibility with
existing installs, project data, automation and deployment configuration. It is not the
public product brand.
"""

from importlib.util import find_spec

__version__ = "0.20.0"


def _install_provider_budget_boundary() -> None:
    """Install renderer budget enforcement only when renderer runtime dependencies exist.

    Security-only and release-trust jobs intentionally import package modules with a minimal
    dependency set. Importing the package must not force the optional ComfyUI/httpx runtime into
    those trust boundaries. A production renderer cannot operate without both dependencies, so
    the budget guard is installed whenever that renderer runtime is actually available.
    """

    if find_spec("httpx") is None or find_spec("pydantic") is None:
        return

    from .provider_budget_enforcement import install_provider_budget_enforcement

    install_provider_budget_enforcement()


_install_provider_budget_boundary()
