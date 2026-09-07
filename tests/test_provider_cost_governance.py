from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.provider_cost_governance as governance
from aura_music_studio.provider_cost_governance import ProviderCostStore, configured_estimate_minor


def test_provider_cost_ledger_hashes_refs_and_is_idempotent(tmp_path):
    db_path = tmp_path / "costs.sqlite3"
    store = ProviderCostStore(db_path)

    first = store.record_submission(
        provider="comfyui",
        service="video",
        operation="create",
        job_ref="provider-job-secret-123",
        user_ref="member-user-raw",
        project_ref="private-project-name",
        media_kind="video",
        unit_name="frames",
        units=121,
        estimated_cost_minor=75,
    )
    second = store.record_submission(
        provider="comfyui",
        service="video",
        operation="create",
        job_ref="provider-job-secret-123",
        user_ref="member-user-raw",
        project_ref="private-project-name",
        media_kind="video",
        unit_name="frames",
        units=121,
        estimated_cost_minor=75,
    )

    assert first["id"] == second["id"]
    assert first["estimated_cost_minor"] == 75
    assert first["actual_cost_minor"] is None

    with sqlite3.connect(db_path) as con:
        raw = "\n".join(str(value) for row in con.execute("SELECT * FROM provider_cost_events") for value in row)
    assert "provider-job-secret-123" not in raw
    assert "member-user-raw" not in raw
    assert "private-project-name" not in raw


def test_actual_reconciliation_replaces_estimate_in_effective_spend(tmp_path):
    store = ProviderCostStore(tmp_path / "costs.sqlite3")
    event = store.record_submission(
        provider="comfyui",
        service="image",
        operation="render",
        job_ref="job-1",
        estimated_cost_minor=40,
    )
    before = store.summary(30)
    assert before["totals"]["spend_minor"] == 40
    assert before["totals"]["estimated_jobs"] == 1

    updated = store.reconcile_actual(event["id"], 27, currency="GBP")
    assert updated["actual_cost_minor"] == 27
    after = store.summary(30)
    assert after["totals"]["spend_minor"] == 27
    assert after["totals"]["actual_jobs"] == 1
    assert after["totals"]["estimated_jobs"] == 0


def test_unpriced_jobs_are_truthful_not_zero_cost_claims(tmp_path, monkeypatch):
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_MINOR", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_MINOR", raising=False)
    store = ProviderCostStore(tmp_path / "costs.sqlite3")
    event = store.record_submission(
        provider="comfyui",
        service="video",
        operation="render",
        job_ref="job-unpriced",
    )
    assert event["estimated_cost_minor"] is None
    summary = store.summary(30)
    assert summary["totals"]["unpriced_jobs"] == 1
    assert summary["spend_basis"] == "actual_where_known_otherwise_operator_estimate"
    assert summary["creation_coin_effect"] == "none"
    assert summary["subscription_effect"] == "none"
    assert summary["esp_role_effect"] == "none"


def test_operator_configured_estimate_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_MINOR", "90")
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_MINOR", "60")
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_CREATE_MINOR", "45")
    assert configured_estimate_minor("comfyui", "video", "create") == 45
    assert configured_estimate_minor("comfyui", "video", "regenerate") == 60
    assert configured_estimate_minor("comfyui", "image", "render") == 90

    store = ProviderCostStore(tmp_path / "costs.sqlite3")
    row = store.record_submission(
        provider="comfyui",
        service="video",
        operation="create",
        job_ref="job-estimated",
    )
    assert row["estimated_cost_minor"] == 45


def test_budget_windows_warn_without_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", "100")
    monkeypatch.setenv("AURA_PROVIDER_COST_BUDGET_MONTHLY_MINOR", "1000")
    monkeypatch.setenv("AURA_PROVIDER_COST_WARNING_PERCENT", "80")
    store = ProviderCostStore(tmp_path / "costs.sqlite3")
    store.record_submission(
        provider="comfyui",
        service="video",
        operation="render",
        job_ref="budget-job",
        estimated_cost_minor=85,
    )
    status = store.budget_status()
    assert status["enforcement"] == "warning_only"
    assert status["windows"]["daily"]["state"] == "warning"
    assert status["windows"]["daily"]["percent_used"] == 85.0
    assert status["windows"]["monthly"]["state"] == "within_budget"


def test_recent_rows_never_return_hashed_identity_fields(tmp_path):
    store = ProviderCostStore(tmp_path / "costs.sqlite3")
    store.record_submission(
        provider="comfyui",
        service="image",
        operation="render",
        job_ref="provider-job",
        user_ref="user-ref",
        project_ref="project-ref",
        estimated_cost_minor=12,
    )
    recent = store.recent()
    assert len(recent) == 1
    assert "user_ref_hash" not in recent[0]
    assert "project_ref_hash" not in recent[0]
    assert "job_ref_hash" not in recent[0]
    assert "event_key_hash" not in recent[0]


def _owner_app(monkeypatch, tmp_path, *, authorized: bool) -> FastAPI:
    monkeypatch.setattr(governance, "store", ProviderCostStore(tmp_path / "owner-costs.sqlite3"))
    monkeypatch.setattr(governance, "owner_session_authorized", lambda request: authorized)
    app = FastAPI()
    app.include_router(governance.router)
    return app


def test_owner_api_is_private_and_does_not_expose_raw_refs(monkeypatch, tmp_path):
    denied = TestClient(_owner_app(monkeypatch, tmp_path, authorized=False))
    assert denied.get("/owner/api/provider-costs/summary").status_code == 403

    app = _owner_app(monkeypatch, tmp_path, authorized=True)
    governance.store.record_submission(
        provider="comfyui",
        service="video",
        operation="render",
        job_ref="raw-provider-job",
        user_ref="raw-user",
        project_ref="raw-project",
        estimated_cost_minor=55,
    )
    response = TestClient(app).get("/owner/api/provider-costs/summary")
    assert response.status_code == 200
    body = response.json()
    serialized = response.text
    assert "raw-provider-job" not in serialized
    assert "raw-user" not in serialized
    assert "raw-project" not in serialized
    assert body["privacy"]["raw_provider_job_refs_exposed"] is False
    assert body["privacy"]["provider_secrets_exposed"] is False


def test_production_app_mounts_cost_governance_and_vercel_is_globally_disabled():
    app_source = Path("app.py").read_text(encoding="utf-8")
    assert "install_provider_cost_governance" in app_source
    assert "install_provider_cost_governance()" in app_source
    assert "app.include_router(provider_cost_governance_router)" in app_source

    vercel = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert vercel["git"]["deploymentEnabled"] is False
