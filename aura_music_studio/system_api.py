from __future__ import annotations

from fastapi import APIRouter

from .doctor import system_report
from .model_catalog import public_catalog
from .provider_credentials import credential_report

router = APIRouter(prefix="/system", tags=["Studio System"])


@router.get("/doctor")
def doctor():
    report = system_report()
    report["credentials"] = credential_report()
    return report


@router.get("/credential-status")
def credential_status():
    """Return configuration state only; never return any secret value or fingerprint."""
    return credential_report()


@router.get("/model-catalog")
def model_catalog():
    return {
        "policy": (
            "ESP Live Sound Studio never assumes that an open repository or downloadable checkpoint is "
            "commercially unrestricted. Code and model/voice licences are tracked separately."
        ),
        "components": public_catalog(),
    }
