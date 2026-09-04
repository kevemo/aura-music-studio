from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


class SharedSkyVaultError(RuntimeError):
    """Raised when Shared Sky secret material cannot be safely used."""


def mask_secret(value: str | None) -> str:
    """Return a non-reversible display hint without exposing secret material."""
    clean = (value or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "••••••••"
    return f"{clean[:3]}••••••{clean[-3:]}"


@dataclass(frozen=True)
class VaultHealth:
    configured: bool
    cipher: str = "Fernet/AES128-CBC+HMAC-SHA256"
    secret_source: str = "SHARED_SKY_VAULT_SECRET"


class SharedSkyVault:
    """Small encryption boundary for destination credentials.

    The configured secret is never persisted by this class. A stable Fernet key is
    derived from ``SHARED_SKY_VAULT_SECRET`` so encrypted destination credentials can
    be stored in the application database while the key remains deployment-managed.
    """

    def __init__(self, secret: str | None = None):
        self._secret = (secret if secret is not None else os.getenv("SHARED_SKY_VAULT_SECRET", "")).strip()
        self._fernet: Fernet | None = None
        if self._secret:
            digest = hashlib.sha256(self._secret.encode("utf-8")).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def health(self) -> VaultHealth:
        return VaultHealth(configured=self.configured)

    def encrypt(self, plaintext: str) -> str:
        clean = (plaintext or "").strip()
        if not clean:
            return ""
        if not self._fernet:
            raise SharedSkyVaultError(
                "Shared Sky secret vault is not configured. Set SHARED_SKY_VAULT_SECRET before storing platform credentials."
            )
        return self._fernet.encrypt(clean.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        token = (ciphertext or "").strip()
        if not token:
            return ""
        if not self._fernet:
            raise SharedSkyVaultError("Shared Sky secret vault is not configured")
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise SharedSkyVaultError("Shared Sky could not decrypt the stored destination credential") from exc


__all__ = ["SharedSkyVault", "SharedSkyVaultError", "VaultHealth", "mask_secret"]
