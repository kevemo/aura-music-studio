from __future__ import annotations

from .aura_connectors import google, vault

_INSTALLED = False


def install_aura_connector_hardening() -> None:
    """Ensure local disconnect remains possible even if connector decryption fails.

    Provider-side revocation needs the decrypted Google token. If the deployment Fernet key
    was lost/rotated incorrectly, that provider call is no longer possible, but the member
    must still be able to remove the unusable encrypted credential from Pulsar-Frequency
    House. This wrapper preserves normal revocation when decryption works and falls back only
    to deleting the local per-member record when it does not.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    original_revoke = google.revoke

    def safe_revoke(user_id: str) -> bool:
        try:
            return original_revoke(user_id)
        except RuntimeError:
            return vault.delete(user_id, "google")

    google.revoke = safe_revoke  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = ["install_aura_connector_hardening"]
