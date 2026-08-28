from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.export_provenance import (
    ExportProvenanceStore,
    OwnerExportReviewInput,
    member_router,
    owner_router,
)


def _record(store: ExportProvenanceStore, tmp_path: Path, *, user_id: str = "u1", commercial: bool = True):
    media = tmp_path / f"{user_id}.png"
    media.write_bytes(b"example-export-bytes")
    return store.record_export(
        user_id=user_id,
        project_name="project-one",
        sequence_id="seq-1",
        filename=media.name,
        media_kind="image",
        format="png",
        path=media,
        commercial_use_requested=commercial,
        rights_attested=commercial,
    )


def test_commercial_export_is_not_auto_cleared(tmp_path):
    store = ExportProvenanceStore(tmp_path / "db.sqlite3")
    row = _record(store, tmp_path)
    assert row["commercial_use_requested"] is True
    assert row["rights_attested"] is True
    assert row["commercial_platform_export_allowed"] is False
    assert row["automatic_legal_clearance"] is False
    assert row["copyrightability_guaranteed"] is False
    assert row["uniqueness_guaranteed"] is False
    assert row["grants_esp_role_or_permission"] is False
    assert row["alters_billing_or_membership"] is False


def test_exact_duplicate_detection_does_not_disclose_other_member(tmp_path):
    store = ExportProvenanceStore(tmp_path / "db.sqlite3")
    first = _record(store, tmp_path, user_id="u1", commercial=False)
    second = _record(store, tmp_path, user_id="u2", commercial=False)
    assert first["internal_exact_duplicate_detected"] is False
    assert second["internal_exact_duplicate_detected"] is True
    assert "duplicate_user_id" not in second
    with pytest.raises(KeyError):
        store.get_for_user(second["id"], "u1")


def test_owner_review_requires_opaque_evidence_and_records_scope(tmp_path):
    store = ExportProvenanceStore(tmp_path / "db.sqlite3")
    row = _record(store, tmp_path)
    with pytest.raises(ValueError):
        store.owner_review(
            row["id"],
            OwnerExportReviewInput(
                status="cleared_for_platform_export",
                review_method="manual_ip_review",
                evidence_reference="https://example.com/evidence",
            ),
        )

    reviewed = store.owner_review(
        row["id"],
        OwnerExportReviewInput(
            status="cleared_for_platform_export",
            review_method="manual_ip_review",
            evidence_reference="ip-review:case-123",
            note="Rights and originality evidence reviewed for platform export.",
        ),
    )
    assert reviewed["commercial_platform_export_allowed"] is True
    assert reviewed["external_similarity_completed"] is False
    assert reviewed["similarity_scope"] == "internal_exact_sha256_only"
    assert reviewed["automatic_legal_clearance"] is False


def test_external_similarity_review_is_evidence_not_legal_guarantee(tmp_path):
    store = ExportProvenanceStore(tmp_path / "db.sqlite3")
    row = _record(store, tmp_path)
    reviewed = store.owner_review(
        row["id"],
        OwnerExportReviewInput(
            status="cleared_for_platform_export",
            review_method="external_similarity_service",
            evidence_reference="similarity:vendor-run-456",
        ),
    )
    assert reviewed["external_similarity_completed"] is True
    assert reviewed["similarity_scope"] == "external_review_recorded"
    assert reviewed["commercial_platform_export_allowed"] is True
    assert reviewed["automatic_legal_clearance"] is False


def test_owner_route_hidden_and_member_route_mounted():
    app = FastAPI()
    app.include_router(member_router)
    app.include_router(owner_router)
    paths = set(app.openapi()["paths"])
    assert "/creative/export-governance/{export_id}" in paths
    assert "/owner/creative/export-governance/{export_id}/review" not in paths

    client = TestClient(app)
    response = client.post(
        "/owner/creative/export-governance/missing/review",
        json={
            "status": "blocked",
            "review_method": "manual_ip_review",
            "evidence_reference": "review:missing",
            "note": "test",
        },
    )
    assert response.status_code == 403


def test_render_request_commercial_rights_contract():
    from aura_music_studio.professional_editor_render_api import EditorRenderRequest

    ordinary = EditorRenderRequest(format="png")
    assert ordinary.commercial_use is False
    assert ordinary.rights_attested is False

    commercial = EditorRenderRequest(format="png", commercial_use=True, rights_attested=True)
    assert commercial.commercial_use is True
    assert commercial.rights_attested is True


def test_integration_overlay_dispatches_export_governance_routes():
    from aura_music_studio.creative_version_autopromotion import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    member_response = client.get("/creative/export-governance/missing")
    assert member_response.status_code == 401

    owner_response = client.post(
        "/owner/creative/export-governance/missing/review",
        json={
            "status": "blocked",
            "review_method": "manual_ip_review",
            "evidence_reference": "review:missing",
            "note": "test",
        },
    )
    assert owner_response.status_code == 403
