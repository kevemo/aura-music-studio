from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_social_access_control import EspSocialAccessControlStore
from aura_music_studio.owner_user_control import OwnerUserControl


def _active_creator(accounts: AccountStore, esp: EspStore):
    signup = accounts.signup(
        "social-audit-creator@example.com",
        "Social Audit Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], "creator", "socialauditcreator", "UK+", "test")
    esp.decide(token, "approve", "creator", "Owner")
    return user


def _context(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)
    social = EspSocialAccessControlStore(esp)
    user = _active_creator(accounts, esp)
    return accounts, esp, control, social, user


def _commercial_state(accounts: AccountStore, user_id: str) -> tuple[str, str, str]:
    user = accounts.get_user(user_id) or {}
    return (
        str(user.get("plan_id") or ""),
        str(user.get("requested_plan_id") or ""),
        str(user.get("billing_status") or ""),
    )


def test_social_suspend_writes_minimized_central_owner_audit(tmp_path):
    accounts, esp, control, social, user = _context(tmp_path)
    private_reason = "Private safeguarding context that must stay out of central audit JSON"
    commercial_before = _commercial_state(accounts, user["id"])
    membership_before = dict(esp.membership(user["id"]) or {})

    result = social.suspend(user["id"], actor="Mary · ESP Owner", reason=private_reason)

    assert result["state"] == "suspended"
    assert result["reason"] == private_reason
    audit = control.audit_log(user["id"], 10)[0]
    assert audit["action"] == "esp_social_access_suspended"
    assert audit["actor"] == "Mary · ESP Owner"
    assert audit["before"]["state"] == "default"
    assert audit["after"]["state"] == "suspended"
    assert audit["metadata"]["access_surface"] == "esp_social_media_centre"
    assert audit["metadata"]["reason_present"] is True
    assert audit["metadata"]["esp_membership_changed"] is False
    assert audit["metadata"]["subscription_changed"] is False
    assert private_reason not in str(audit)
    assert _commercial_state(accounts, user["id"]) == commercial_before
    assert esp.membership(user["id"]) == membership_before


def test_social_restore_records_suspended_to_default_transition(tmp_path):
    _accounts, _esp, control, social, user = _context(tmp_path)
    social.suspend(user["id"], actor="Kev · ESP Owner", reason="temporary owner suspension")

    result = social.restore(user["id"], actor="Kev · ESP Owner")

    assert result["state"] == "default"
    assert result["reason"] == ""
    audit = control.audit_log(user["id"], 10)[0]
    assert audit["action"] == "esp_social_access_restored"
    assert audit["actor"] == "Kev · ESP Owner"
    assert audit["before"]["state"] == "suspended"
    assert audit["after"]["state"] == "default"
    assert audit["metadata"]["reason_present"] is True


def test_social_control_rejects_unknown_esp_member_without_audit(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)
    social = EspSocialAccessControlStore(esp)

    with pytest.raises(ValueError, match="ESP membership not found"):
        social.suspend("missing-user", actor="ESP Owner", reason="blocked")

    assert control.audit_log(limit=10) == []


def test_social_access_change_rolls_back_if_owner_audit_write_fails(tmp_path, monkeypatch):
    _accounts, _esp, control, social, user = _context(tmp_path)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(OwnerUserControl, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        social.suspend(user["id"], actor="ESP Owner", reason="must roll back")

    assert social.get(user["id"])["state"] == "default"
    assert control.audit_log(user["id"], 10) == []


def test_social_audit_snapshot_never_contains_reason_text():
    snapshot = EspSocialAccessControlStore._audit_snapshot(
        {
            "user_id": "creator-1",
            "state": "suspended",
            "reason": "sensitive internal reason",
            "updated_by": "ESP Owner",
            "updated_at": "2026-08-29T00:00:00+00:00",
        }
    )
    assert "reason" not in snapshot
    assert "sensitive internal reason" not in str(snapshot)
