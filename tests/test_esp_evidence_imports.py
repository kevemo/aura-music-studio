from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_evidence_import_portal import router as portal_router
from aura_music_studio.esp_evidence_imports import (
    EvidenceImportCommit,
    EvidenceImportStore,
    MappingTemplateCreate,
    preview_uploaded_evidence,
    router,
)
from aura_music_studio.esp_product_workflows import Chat9WorkflowStore


def _active_user(accounts: AccountStore, email: str, display: str) -> dict:
    signup = accounts.signup(email, display, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _esp_role(esp: EspStore, user_id: str, role: str, *, status: str = "active", region: str = "UK+") -> None:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,?,?,?,?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,region=excluded.region,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (user_id, status, role, "", region),
        )


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-imports.sqlite3")
    esp = EspStore(accounts)
    workflows = Chat9WorkflowStore(esp)
    return accounts, esp, workflows, EvidenceImportStore(workflows)


def test_csv_preview_detects_explicit_metric_value_columns_without_auto_commit():
    data = (
        b"Metric,Value,Unit\n"
        b"Live Hours,42.5,hours\n"
        b"Retention,,percent\n"
    )
    preview = preview_uploaded_evidence(
        data,
        source_type="csv",
        filename="creator-export.csv",
        content_type="text/csv",
    )

    assert preview["auto_commit"] is False
    assert preview["human_review_required"] is True
    assert preview["mapping_required"] is False
    assert preview["source_sha256"]
    assert [item["name"] for item in preview["candidate_metrics"]] == ["live_hours", "retention"]
    assert preview["candidate_metrics"][0]["value"] == 42.5
    assert preview["candidate_metrics"][1]["value"] is None
    assert all(item["review_required_before_commit"] for item in preview["candidate_metrics"])


def test_xlsx_preview_uses_saved_mapping_and_preserves_explicit_cells(tmp_path):
    accounts, esp, _workflows, imports = _stores(tmp_path)
    creator = _active_user(accounts, "mapping@example.com", "Mapping Creator")
    _esp_role(esp, creator["id"], "creator")

    mapping = imports.create_mapping(
        creator["id"],
        MappingTemplateCreate(
            name="Provider export",
            source_type="xlsx",
            metric_column="Label",
            value_column="Result",
            unit_column="Unit",
        ),
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Label", "Result", "Unit"])
    sheet.append(["Valid Days", 12, "days"])
    sheet.append(["Live Hours", 37.25, "hours"])
    payload = io.BytesIO()
    workbook.save(payload)
    workbook.close()

    preview = preview_uploaded_evidence(
        payload.getvalue(),
        source_type="xlsx",
        filename="provider.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        mapping=mapping,
    )

    assert preview["mapping_required"] is False
    assert [item["name"] for item in preview["candidate_metrics"]] == ["valid_days", "live_hours"]
    assert preview["candidate_metrics"][0]["value"] == 12
    assert preview["candidate_metrics"][1]["value"] == 37.25


def test_reviewed_preview_commit_is_durable_and_sha_deduplicated(tmp_path):
    accounts, esp, workflows, imports = _stores(tmp_path)
    creator = _active_user(accounts, "import@example.com", "Import Creator")
    _esp_role(esp, creator["id"], "creator")

    source = b"Metric,Value,Unit\nLive Hours,40,hours\nValid Days,9,days\n"
    parsed = preview_uploaded_evidence(
        source,
        source_type="csv",
        filename="august.csv",
        content_type="text/csv",
    )
    preview = imports.create_preview(
        actor_user_id=creator["id"],
        creator_user_id=creator["id"],
        source_type="csv",
        provider="TikTok LIVE Studio export",
        raw_evidence_ref="asset:evidence:august-creator-export",
        preview=parsed,
    )

    batch = imports.commit_preview(
        actor_user_id=creator["id"],
        body=EvidenceImportCommit(
            preview_id=preview["id"],
            selected_metric_names=["live_hours"],
            period_start="2026-08-01",
            period_end="2026-08-31",
            captured_at="2026-08-31T23:00:00+00:00",
            notes="Reviewed against source export.",
        ),
    )

    assert batch["source_type"] == "csv"
    assert batch["imported_snapshot"] is True
    assert batch["realtime"] is False
    assert batch["raw_evidence_ref"] == "asset:evidence:august-creator-export"
    assert [(item["metric_name"], item["value"]) for item in batch["metrics"]] == [("live_hours", 40)]

    duplicate_preview = imports.create_preview(
        actor_user_id=creator["id"],
        creator_user_id=creator["id"],
        source_type="csv",
        provider="TikTok LIVE Studio export",
        raw_evidence_ref="asset:evidence:august-creator-export",
        preview=parsed,
    )
    assert duplicate_preview["duplicate_source"] is True
    assert duplicate_preview["existing_batch_id"] == batch["id"]

    with pytest.raises(FileExistsError, match="duplicate_import"):
        imports.commit_preview(
            actor_user_id=creator["id"],
            body=EvidenceImportCommit(
                preview_id=duplicate_preview["id"],
                selected_metric_names=["live_hours"],
            ),
        )

    assert len(workflows.evidence_for_creator(creator["id"])) == 1


def test_import_target_must_be_an_esp_creator(tmp_path):
    accounts, esp, _workflows, imports = _stores(tmp_path)
    agent = _active_user(accounts, "agent-only@example.com", "Agent Only")
    _esp_role(esp, agent["id"], "agent")

    parsed = preview_uploaded_evidence(
        b"Metric,Value\nLive Hours,10\n",
        source_type="csv",
        filename="agent.csv",
        content_type="text/csv",
    )
    with pytest.raises(ValueError, match="ESP Creator target"):
        imports.create_preview(
            actor_user_id=agent["id"],
            creator_user_id=agent["id"],
            source_type="csv",
            provider="manual export",
            raw_evidence_ref="asset:evidence:not-a-creator",
            preview=parsed,
        )


def test_evidence_import_routes_and_portal_are_wired():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/workflows/evidence/mappings" in paths
    assert "/command-center/api/workflows/evidence/import-preview" in paths
    assert "/command-center/api/workflows/evidence/import-commit" in paths

    portal_paths = {getattr(route, "path", None) for route in portal_router.routes}
    assert "/command-center/evidence-import" in portal_paths
