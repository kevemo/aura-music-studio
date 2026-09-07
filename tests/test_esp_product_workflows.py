from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import (
    AnnouncementCreate,
    Chat9WorkflowStore,
    CreatorProfileUpdate,
    EvidenceBatchInput,
    EvidenceMetricInput,
    LeadCreate,
    LeadUpdate,
    MetricCorrection,
    StaleVersionError,
    router,
)


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


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "chat9.sqlite3")
    esp = EspStore(accounts)
    return accounts, esp, Chat9WorkflowStore(esp)


def test_creator_profile_separates_public_and_private_fields_and_uses_version_checks(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    creator = _active_user(accounts, "creator@example.com", "Creator")
    _esp_role(esp, creator["id"], "creator")

    saved = workflows.save_profile(
        creator["id"],
        CreatorProfileUpdate(
            expected_version=0,
            public_display_name="Creator Public",
            bio="Public biography",
            public_region="United Kingdom",
            languages=["English"],
            primary_niche="music",
            public_social_links={"tiktok": "https://example.invalid/@creator"},
            discoverable=True,
            timezone="Europe/London",
            live_experience="Private mentoring context",
            goals=["Private growth goal"],
            schedule={"monday": "19:00"},
            equipment=["Private equipment inventory"],
            specialisms=["music"],
            acknowledgements={"standards_v1": True},
            onboarding_status="complete",
        ),
        actor=creator["id"],
    )

    assert saved["version"] == 1
    assert saved["goals"] == ["Private growth goal"]
    public = workflows.public_profile(creator["id"])
    assert public["display_name"] == "Creator Public"
    assert "goals" not in public
    assert "equipment" not in public
    assert "acknowledgements" not in public
    assert "timezone" not in public

    with pytest.raises(StaleVersionError):
        workflows.save_profile(
            creator["id"],
            CreatorProfileUpdate(expected_version=0, public_display_name="Stale write"),
            actor=creator["id"],
        )


def test_public_profile_disappears_when_creator_role_is_revoked(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    creator = _active_user(accounts, "public@example.com", "Public Creator")
    _esp_role(esp, creator["id"], "creator")
    workflows.save_profile(
        creator["id"],
        CreatorProfileUpdate(expected_version=0, public_display_name="Public Creator", discoverable=True),
        actor=creator["id"],
    )
    assert workflows.public_profile(creator["id"]) is not None

    _esp_role(esp, creator["id"], "", status="revoked")
    assert workflows.public_profile(creator["id"]) is None


def test_evidence_import_preserves_provenance_missing_values_and_corrections(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    creator = _active_user(accounts, "evidence@example.com", "Evidence Creator")
    agent = _active_user(accounts, "agent@example.com", "Agent")
    _esp_role(esp, creator["id"], "creator")
    _esp_role(esp, agent["id"], "agent")

    batch = workflows.create_evidence(
        creator["id"],
        EvidenceBatchInput(
            source_type="screenshot",
            provider="TikTok LIVE Studio",
            period_start="2026-08-01",
            period_end="2026-08-31",
            captured_at="2026-08-31T23:00:00+00:00",
            raw_evidence_ref="asset:evidence:abc123",
            notes="Uploaded snapshot; not realtime.",
            metrics=[
                EvidenceMetricInput(name="live_hours", value=42.5, unit="hours", confidence=0.82),
                EvidenceMetricInput(name="retention", value=None, unit="percent", confidence=None),
            ],
        ),
        uploader_user_id=creator["id"],
    )

    assert batch["source_type"] == "screenshot"
    assert batch["raw_evidence_ref"] == "asset:evidence:abc123"
    assert batch["imported_snapshot"] is True
    assert batch["realtime"] is False
    missing = next(metric for metric in batch["metrics"] if metric["metric_name"] == "retention")
    assert missing["value"] is None
    assert missing["needs_review"] is True

    live_hours = next(metric for metric in batch["metrics"] if metric["metric_name"] == "live_hours")
    corrected = workflows.correct_metric(
        live_hours["id"],
        MetricCorrection(expected_version=1, value=43.0, unit="hours", confidence=1.0, reason="Human checked source image"),
        actor=agent["id"],
    )
    updated_metric = next(metric for metric in corrected["metrics"] if metric["metric_name"] == "live_hours")
    assert updated_metric["value"] == 43.0
    assert updated_metric["version"] == 2
    assert corrected["raw_evidence_ref"] == "asset:evidence:abc123"

    with workflows._connect() as con:
        corrections = con.execute(
            "SELECT COUNT(*) n FROM esp_creator_evidence_corrections WHERE metric_id=?", (live_hours["id"],)
        ).fetchone()["n"]
    assert corrections == 1


def test_lead_crm_deduplicates_globally_and_enforces_do_not_contact(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    first_agent = _active_user(accounts, "agent1@example.com", "Agent One")
    second_agent = _active_user(accounts, "agent2@example.com", "Agent Two")
    _esp_role(esp, first_agent["id"], "agent")
    _esp_role(esp, second_agent["id"], "agent")

    payload = LeadCreate(
        platform="TikTok",
        handle="@Example.Creator",
        public_profile_url="https://example.invalid/example.creator",
        region="UK+",
        niche="music",
        source="manual_public_discovery",
    )
    lead = workflows.create_lead(payload, actor_user_id=first_agent["id"], assigned_agent_user_id=first_agent["id"])
    assert lead["handle"] == "example.creator"
    assert lead["assigned_agent_user_id"] == first_agent["id"]

    with pytest.raises(FileExistsError, match="duplicate_lead"):
        workflows.create_lead(payload, actor_user_id=second_agent["id"], assigned_agent_user_id=second_agent["id"])

    blocked = workflows.update_lead(
        lead["id"],
        LeadUpdate(expected_version=1, do_not_contact=True, status="contacted"),
        actor_user_id=first_agent["id"],
        require_assignee=first_agent["id"],
    )
    assert blocked["do_not_contact"] is True
    assert blocked["status"] == "do_not_contact"
    assert len(blocked["history"]) == 2

    with pytest.raises(PermissionError):
        workflows.update_lead(
            lead["id"],
            LeadUpdate(expected_version=2, status="follow_up"),
            actor_user_id=second_agent["id"],
            require_assignee=second_agent["id"],
        )


def test_announcement_publish_requires_confirmation_and_targeting(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    owner = _active_user(accounts, "owner@example.com", "Owner")
    creator = _active_user(accounts, "creator2@example.com", "Creator")
    agent = _active_user(accounts, "agent3@example.com", "Agent")
    _esp_role(esp, owner["id"], "owner", status="owner", region="Global")
    _esp_role(esp, creator["id"], "creator", region="UK+")
    _esp_role(esp, agent["id"], "agent", region="USA")

    with pytest.raises(PermissionError, match="high_impact_confirmation_required"):
        workflows.create_announcement(
            AnnouncementCreate(title="Policy", body="Important update", audience="region", audience_value="UK+", status="published"),
            actor_user_id=owner["id"],
        )

    announcement = workflows.create_announcement(
        AnnouncementCreate(
            title="UK creator update",
            body="Important update",
            audience="region",
            audience_value="UK+",
            acknowledgement_required=True,
            status="published",
            confirm_publish=True,
            reason="Owner-approved operational announcement",
        ),
        actor_user_id=owner["id"],
    )
    assert announcement["status"] == "published"
    creator_membership = esp.membership(creator["id"])
    agent_membership = esp.membership(agent["id"])
    assert [row["id"] for row in workflows.visible_announcements(creator["id"], creator_membership)] == [announcement["id"]]
    assert workflows.visible_announcements(agent["id"], agent_membership) == []


def test_router_exposes_creator_evidence_crm_and_public_identity_contracts():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/workflows/creator-profile" in paths
    assert "/shared-sky/public/creators/{creator_user_id}" in paths
    assert "/command-center/api/workflows/evidence" in paths
    assert "/command-center/api/workflows/leads" in paths
    assert "/command-center/api/workflows/announcements" in paths
