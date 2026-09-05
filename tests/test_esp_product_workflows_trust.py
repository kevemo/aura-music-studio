from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import Chat9WorkflowStore, EvidenceBatchInput
import aura_music_studio.esp_product_workflows_hardening  # noqa: F401


def _active_user(accounts: AccountStore) -> dict:
    signup = accounts.signup(
        "trusted-evidence@example.com",
        "Trusted Evidence Creator",
        "a-very-secure-test-password",
        "free",
    )
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _creator_role(esp: EspStore, user_id: str) -> None:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,'active','creator','','UK+',datetime('now'),'test-owner',datetime('now'))""",
            (user_id,),
        )


def test_member_cannot_self_assert_trusted_machine_evidence_source(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-trust.sqlite3")
    esp = EspStore(accounts)
    creator = _active_user(accounts)
    _creator_role(esp, creator["id"])
    workflows = Chat9WorkflowStore(esp)

    payload = EvidenceBatchInput(
        source_type="provider_api",
        provider="TikTok",
        raw_evidence_ref="provider:evidence:one",
    )
    with pytest.raises(ValueError, match="reserved for authenticated server adapters"):
        workflows.create_evidence(
            creator["id"],
            payload,
            uploader_user_id=creator["id"],
        )


def test_authenticated_service_boundary_can_record_trusted_machine_evidence(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-trust-service.sqlite3")
    esp = EspStore(accounts)
    creator = _active_user(accounts)
    _creator_role(esp, creator["id"])
    workflows = Chat9WorkflowStore(esp)

    payload = EvidenceBatchInput(
        source_type="shared_sky",
        provider="Shared Sky",
        raw_evidence_ref="shared-sky:evidence:session-1",
    )
    batch = workflows.create_evidence(
        creator["id"],
        payload,
        uploader_user_id=creator["id"],
        trusted_source=True,
    )
    assert batch["source_type"] == "shared_sky"
    assert batch["realtime"] is True
    assert batch["imported_snapshot"] is False
