from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_data_import import CreatorDataImportStore
from aura_music_studio.esp_creator_data_import_overlay import router as import_router
from aura_music_studio.esp_creator_import_governance import (
    CreatorImportGovernanceStore,
    ImportMappingTemplateCreate,
    ImportProvenanceInput,
    router as governance_router,
)
from aura_music_studio.esp_progress import EspProgressStore


def _stores(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_PROGRESS_ROOT", str(tmp_path / "progress-files"))
    accounts = AccountStore(tmp_path / "governance.sqlite3")
    signup = accounts.signup(
        "creator@example.com",
        "Creator",
        "a-very-secure-test-password",
        "base",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    progress = EspProgressStore(esp)
    imports = CreatorDataImportStore(str(accounts.db_path), progress)
    governance = CreatorImportGovernanceStore(str(accounts.db_path), AuditLedger(accounts))
    return accounts, user, imports, governance


def test_source_sha_prevents_duplicate_staging_and_retains_snapshot_provenance(tmp_path, monkeypatch):
    _accounts, user, imports, governance = _stores(tmp_path, monkeypatch)
    content = b"Date,Views,Shares\n2026-08-25,1200,14\n"
    provenance = ImportProvenanceInput(
        provider="TikTok LIVE Studio export",
        source_label="August analytics",
        captured_at="2026-08-31T23:00:00+00:00",
        period_start="2026-08-01",
        period_end="2026-08-31",
    )

    reservation = governance.reserve_source(
        user_id=user["id"],
        content=content,
        original_filename="august.csv",
        content_type="text/csv",
        provenance=provenance,
    )
    staged = imports.stage(user["id"], "august.csv", content, "text/csv")
    attached = governance.attach_import(
        user_id=user["id"],
        digest=reservation["source_sha256"],
        import_id=staged["id"],
    )

    assert attached["source_sha256"] == reservation["source_sha256"]
    assert attached["provider"] == "TikTok LIVE Studio export"
    assert attached["source_label"] == "August analytics"
    assert attached["captured_at"] == "2026-08-31T23:00:00+00:00"
    assert attached["period_start"] == "2026-08-01"
    assert attached["period_end"] == "2026-08-31"
    assert attached["imported_snapshot"] is True
    assert attached["realtime"] is False
    assert attached["direct_backstage_access"] is False
    assert "upload_path" not in attached

    with pytest.raises(FileExistsError, match="duplicate_import"):
        governance.reserve_source(
            user_id=user["id"],
            content=content,
            original_filename="same-file-renamed.csv",
            content_type="text/csv",
            provenance=ImportProvenanceInput(),
        )


def test_failed_stage_reservation_can_be_released_and_retried(tmp_path, monkeypatch):
    _accounts, user, _imports, governance = _stores(tmp_path, monkeypatch)
    content = b"Views\n100\n"
    first = governance.reserve_source(
        user_id=user["id"],
        content=content,
        original_filename="retry.csv",
        content_type="text/csv",
        provenance=ImportProvenanceInput(),
    )
    governance.release_source(user_id=user["id"], digest=first["source_sha256"])
    second = governance.reserve_source(
        user_id=user["id"],
        content=content,
        original_filename="retry.csv",
        content_type="text/csv",
        provenance=ImportProvenanceInput(),
    )
    assert second["source_sha256"] == first["source_sha256"]


def test_same_source_hash_is_tenant_scoped(tmp_path, monkeypatch):
    accounts, user, _imports, governance = _stores(tmp_path, monkeypatch)
    signup = accounts.signup(
        "other@example.com",
        "Other Creator",
        "another-secure-test-password",
        "base",
    )
    other = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    content = b"Views\n100\n"

    one = governance.reserve_source(
        user_id=user["id"], content=content, original_filename="one.csv", content_type="text/csv",
        provenance=ImportProvenanceInput(),
    )
    two = governance.reserve_source(
        user_id=other["id"], content=content, original_filename="two.csv", content_type="text/csv",
        provenance=ImportProvenanceInput(),
    )
    assert one["source_sha256"] == two["source_sha256"]
    assert one["user_id"] != two["user_id"]


def test_saved_mapping_reuses_existing_confirmation_validation_and_is_private(tmp_path, monkeypatch):
    accounts, user, _imports, governance = _stores(tmp_path, monkeypatch)
    mapping = governance.create_mapping(
        user["id"],
        ImportMappingTemplateCreate(
            name="Monthly LIVE export",
            source_format="csv",
            kind="live",
            mapping={"views": "views", "shares": "share_count"},
            period_column="date",
            default_period_label="Monthly LIVE analytics",
        ),
    )
    assert mapping["mapping"] == {"shares": "share_count", "views": "views"}
    assert mapping["kind"] == "live"
    assert governance.resolve_mapping(user["id"], mapping["id"], "csv")["id"] == mapping["id"]

    signup = accounts.signup("other2@example.com", "Other", "another-secure-test-password", "base")
    other = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    with pytest.raises(KeyError):
        governance.mapping(other["id"], mapping["id"])
    with pytest.raises(ValueError, match="source format"):
        governance.resolve_mapping(user["id"], mapping["id"], "xlsx")
    with pytest.raises(ValueError, match="supported progress metric"):
        governance.create_mapping(
            user["id"],
            ImportMappingTemplateCreate(
                name="Bad mapping",
                source_format="csv",
                mapping={"private_unavailable_metric": "secret"},
            ),
        )


def test_governed_overlay_replaces_browser_stage_routes_without_removing_private_api():
    route_methods = [
        (getattr(route, "path", None), set(getattr(route, "methods", set()) or set()))
        for route in import_router.routes
    ]
    stage_gets = [(p, m) for p, m in route_methods if p == "/command-center/progress/import" and "GET" in m]
    stage_posts = [(p, m) for p, m in route_methods if p == "/command-center/progress/import" and "POST" in m]
    review_gets = [
        (p, m) for p, m in route_methods
        if p == "/command-center/progress/import/{import_id}" and "GET" in m
    ]
    assert len(stage_gets) == 1
    assert len(stage_posts) == 1
    assert len(review_gets) == 1
    assert any(
        p == "/command-center/api/progress/imports/{import_id}/confirm" and "POST" in m
        for p, m in route_methods
    )
    governance_paths = {getattr(route, "path", None) for route in governance_router.routes}
    assert "/command-center/api/progress/import-mappings" in governance_paths
    assert "/command-center/api/progress/imports/{import_id}/provenance" in governance_paths
