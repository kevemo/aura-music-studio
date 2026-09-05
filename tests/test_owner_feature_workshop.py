from __future__ import annotations

import inspect

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.owner_feature_workshop import OwnerFeatureWorkshopStore, router


def _store(tmp_path):
    return OwnerFeatureWorkshopStore(AccountStore(tmp_path / "workshop.sqlite3"))


def test_aura_change_plan_preserves_branch_pr_ci_boundaries(tmp_path):
    store = _store(tmp_path)
    item = store.create_change(
        title="Improve video caption editor",
        request_text="Add a safer bulk caption timing adjustment control in the existing editor.",
        subsystem="video",
        change_type="enhancement",
        acceptance_text="Captions can be offset in a bounded range\nExisting caption export still works",
    )
    assert item["status"] == "planned"
    assert item["branch_name"].startswith("owner-update/")
    assert item["aura_plan"]["browser_executes_code"] is False
    assert item["aura_plan"]["production_auto_deploy"] is False
    steps = " ".join(item["aura_plan"]["implementation_steps"]).lower()
    assert "pr" in steps or "pull request" in steps
    assert "development/full-site-build" in steps
    assert "ci" in steps


def test_sensitive_change_is_risk_flagged_and_requires_owner_approval(tmp_path):
    store = _store(tmp_path)
    item = store.create_change(
        title="Billing webhook update",
        request_text="Change payment webhook verification and production billing behavior.",
        subsystem="billing",
        change_type="maintenance",
    )
    assert item["risk_level"] in {"medium", "high"}
    assert item["aura_plan"]["requires_human_release_approval"] is True
    with pytest.raises(ValueError, match="Owner approval"):
        store.queue_execution(item["id"])


def test_approved_change_queues_bounded_worker_payload_without_main(tmp_path):
    store = _store(tmp_path)
    item = store.create_change(
        title="Add creator dashboard filter",
        request_text="Add an owner-requested filter to the existing creator dashboard.",
        subsystem="esp",
        change_type="new_feature",
    )
    store.approve(item["id"])
    queued = store.queue_execution(item["id"])
    assert queued["payload"]["target_branch"] == "development/full-site-build"
    assert queued["payload"]["forbid_main"] is True
    assert queued["payload"]["branch"].startswith("owner-update/")
    assert len(queued["payload_digest"]) == 64


def test_worker_endpoints_fail_closed_without_configured_secret(tmp_path, monkeypatch):
    store = _store(tmp_path)
    item = store.create_change(
        title="Small content update",
        request_text="Update an existing help description without changing permissions.",
        subsystem="core",
        change_type="content",
    )
    store.approve(item["id"])
    queued = store.queue_execution(item["id"])
    monkeypatch.delenv("LSS_OWNER_MAINTENANCE_WORKER_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="authorization required"):
        store.worker_payload(queued["execution_id"], "anything")


def test_trusted_worker_can_report_pr_and_validation_evidence(tmp_path, monkeypatch):
    store = _store(tmp_path)
    item = store.create_change(
        title="Image workflow label",
        request_text="Improve an existing image workflow label and add regression coverage.",
        subsystem="image",
        change_type="enhancement",
    )
    store.approve(item["id"])
    queued = store.queue_execution(item["id"])
    monkeypatch.setenv("LSS_OWNER_MAINTENANCE_WORKER_TOKEN", "test-worker-secret")
    payload = store.worker_payload(queued["execution_id"], "test-worker-secret")
    assert payload["forbid_main"] is True
    result = store.worker_report(
        queued["execution_id"],
        token="test-worker-secret",
        state="validated",
        worker_ref="worker-job-123",
        pr_number=999,
        head_sha="a" * 40,
        ci_state="success",
        security_state="success",
        deployment_state="not_requested",
    )
    assert result["status"] == "validated"
    assert result["executions"][0]["pr_number"] == 999
    assert result["executions"][0]["ci_state"] == "success"


def test_owner_update_routes_exist_and_are_composed_into_base_api():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/updates" in paths
    assert "/owner/feature-workshop" in paths
    assert "/owner/updates/{change_id}" in paths
    assert "/owner-maintenance/worker/{execution_id}" in paths

    import aura_music_studio.api as aggregate
    source = inspect.getsource(aggregate)
    assert "owner_feature_workshop_router" in source
    assert "app.include_router(owner_feature_workshop_router)" in source


def test_owner_navigation_has_updates_and_features_tab():
    from aura_music_studio.owner_control_center import _page

    source = inspect.getsource(_page)
    assert "href='/owner/updates'" in source
    assert "Updates & Features" in source
