from __future__ import annotations

from fastapi import APIRouter

from .doctor import system_report
from .model_catalog import public_catalog

router = APIRouter(prefix="/system", tags=["Studio System"])


@router.get("/doctor")
def doctor():
    return system_report()


@router.get("/model-catalog")
def model_catalog():
    return {
        "policy": (
            "ESP Live Sound Studio never assumes that an open repository or downloadable checkpoint is "
            "commercially unrestricted. Code and model/voice licences are tracked separately."
        ),
        "components": public_catalog(),
    }
