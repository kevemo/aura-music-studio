"""Compatibility shim for the consolidated member account-security surface.

Password reset, recovery UI and session management are owned by
``aura_music_studio.account_security_api`` and are mounted once through the shared
security composition. This module remains importable because an earlier Core increment
mounted ``router`` from here through ``api.py``; keeping an empty router avoids import
churn while guaranteeing there is only one authoritative authentication route set.
"""

from fastapi import APIRouter

router = APIRouter()

__all__ = ["router"]
