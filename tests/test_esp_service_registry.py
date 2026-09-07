from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_service_registry import (
    AGENCY_CAPABILITIES,
    CapabilityEvidenceCreate,
    CapabilityUpdate,
    EspServiceRegistryStore,
)


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    return accounts, esp, EspServiceRegistryStore(esp)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def test_registry_contains_all_94_blueprint_items(tmp_path):
    _accounts, _esp, store = _stores(tmp_path)
    assert len(AGENCY_CAPABILITIES) == 94
    rows = store.list()
    assert len(rows) == 94
    assert rows[0]["id"] == "agency.01"
    assert rows[-1]["id"] == "agency.94"
    assert {row["status"] for row in rows} == {"DESIGNED"}


def test_live_or_scaled_requires_verified_evidence(tmp_path):
    accounts, _esp, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")

    with pytest.raises(ValueError, match="verified evidence"):
        store.update(
            "agency.07",
            CapabilityUpdate(status="LIVE", accountable_owner="Support Ops"),
            actor=owner["id"],
        )

    store.add_evidence(
        "agency.07",
        CapabilityEvidenceCreate(
            evidence_type="pilot",
            label="Pilot completed",
            reference="internal://support-pilot-2026-09",
            verified=True,
        ),
        actor=owner["id"],
    )
    row = store.update(
        "agency.07",
        CapabilityUpdate(status="LIVE", accountable_owner="Support Ops", public_claim_allowed=True),
        actor=owner["id"],
    )
    assert row["status"] == "LIVE"
    assert row["public_claim_allowed"] is True
    assert row["verified_evidence_count"] == 1


def test_public_claim_cannot_be_enabled_for_designed_capability(tmp_path):
    accounts, _esp, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    store.add_evidence(
        "agency.28",
        CapabilityEvidenceCreate(label="CRM schema", reference="internal://crm-schema", verified=True),
        actor=owner["id"],
    )
    with pytest.raises(ValueError, match="LIVE or SCALED"):
        store.update(
            "agency.28",
            CapabilityUpdate(status="DESIGNED", public_claim_allowed=True),
            actor=owner["id"],
        )


def test_capability_audit_hash_chains_updates_and_evidence(tmp_path):
    accounts, _esp, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    store.add_evidence(
        "agency.12",
        CapabilityEvidenceCreate(label="Tech vault pilot", reference="internal://vault-pilot", verified=True),
        actor=owner["id"],
    )
    store.update(
        "agency.12",
        CapabilityUpdate(status="PILOT", accountable_owner="Broadcast Team"),
        actor=owner["id"],
    )
    with store._connect() as con:
        rows = con.execute(
            "SELECT previous_hash,event_hash FROM esp_service_capability_audit WHERE capability_id=? ORDER BY created_at,id",
            ("agency.12",),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["previous_hash"] == ""
    assert rows[1]["previous_hash"] == rows[0]["event_hash"]


def test_summary_reports_domains_and_statuses(tmp_path):
    _accounts, _esp, store = _stores(tmp_path)
    summary = store.summary()
    assert summary["total"] == 94
    assert summary["statuses"]["DESIGNED"] == 94
    assert summary["domains"]["creator_os"] >= 2
    assert summary["domains"]["brand_revenue"] >= 8
