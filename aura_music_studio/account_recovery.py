"""Compatibility bridge for the consolidated member account-security surface.

Password reset, recovery UI and session management are owned by
``aura_music_studio.account_security_api``. The shared API composition historically
imports ``router`` from this module, so re-export the authoritative router here rather
than defining a second set of handlers. This keeps one route owner while preserving the
stable integration seam used by ``aura_music_studio.api`` and the production app.
"""

from .account_security_api import router

__all__ = ["router"]
