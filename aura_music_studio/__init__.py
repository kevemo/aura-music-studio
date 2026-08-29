"""Pulsar-Frequency House — Powered by Elevate Souls Productions & Aura AI Systems.

For Professional Creation Beyond The Cosmos.

The ``aura_music_studio`` package identifier remains for backwards compatibility.
"""

__version__ = "0.20.0"

# Install the professional-editor item patch source guard at package import so the guarded
# PATCH route is ordered ahead of the legacy generic patch route when production mounts the
# editor router. This preserves project-source confinement before render/export is introduced.
from .professional_editor_security_overlay import install_professional_editor_patch_guard as _install_editor_patch_guard

_install_editor_patch_guard()
del _install_editor_patch_guard
