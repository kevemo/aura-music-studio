from __future__ import annotations

import pytest
from fastapi import HTTPException

from aura_music_studio.autotune import AutoTuneSettings
from aura_music_studio.engineering_job_api import _validate_entitlement
from aura_music_studio.engineering_jobs import EngineeringJobRequest
from aura_music_studio.plans import get_plan


class _Member:
    def __init__(self, plan_id: str):
        self.plan = get_plan(plan_id)


def test_base_can_queue_four_stem_split_but_not_detailed_split():
    base = _Member("base")
    _validate_entitlement(base, EngineeringJobRequest(operation="split", asset_id="a", split_mode="four_stems"))
    with pytest.raises(HTTPException) as exc:
        _validate_entitlement(base, EngineeringJobRequest(operation="split", asset_id="a", split_mode="detailed"))
    assert exc.value.status_code == 403


def test_pro_can_queue_detailed_split():
    _validate_entitlement(
        _Member("pro"),
        EngineeringJobRequest(operation="split", asset_id="a", split_mode="detailed"),
    )


def test_base_gets_standard_tune_but_robot_is_pro():
    base = _Member("base")
    _validate_entitlement(
        base,
        EngineeringJobRequest(
            operation="autotune",
            asset_id="a",
            tune_settings=AutoTuneSettings(mode="hard"),
        ),
    )
    with pytest.raises(HTTPException) as exc:
        _validate_entitlement(
            base,
            EngineeringJobRequest(
                operation="autotune",
                asset_id="a",
                tune_settings=AutoTuneSettings(mode="robot"),
            ),
        )
    assert exc.value.status_code == 403


def test_manual_master_controls_and_reference_are_pro():
    base = _Member("base")
    with pytest.raises(HTTPException):
        _validate_entitlement(
            base,
            EngineeringJobRequest(operation="master", asset_id="a", stereo_width=1.3),
        )
    with pytest.raises(HTTPException):
        _validate_entitlement(
            base,
            EngineeringJobRequest(operation="master", asset_id="a", reference_asset_id="ref"),
        )
    _validate_entitlement(
        _Member("pro"),
        EngineeringJobRequest(
            operation="master",
            asset_id="a",
            reference_asset_id="ref",
            stereo_width=1.3,
            target_lufs=-12.0,
        ),
    )


def test_spatial_is_pro_only():
    with pytest.raises(HTTPException):
        _validate_entitlement(
            _Member("base"), EngineeringJobRequest(operation="spatial", asset_id="a")
        )
    _validate_entitlement(
        _Member("pro"), EngineeringJobRequest(operation="spatial", asset_id="a")
    )
