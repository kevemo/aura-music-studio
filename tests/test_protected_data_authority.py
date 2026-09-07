from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio import protected_data_authority as pda


class Audit:
    def __init__(self, fail=False):
        self.fail = fail
        self.entries = []

    def append(self, **entry):
        if self.fail:
            raise RuntimeError("ledger unavailable")
        self.entries.append(entry)


def request():
    return SimpleNamespace(cookies={})


def test_requires_owner_session_and_admitted_persona(monkeypatch):
    monkeypatch.setenv("AURA_SEC_PROTECTED_DATA_PERSONAS", "mary,kev")
    monkeypatch.setattr(pda, "owner_session_authorized", lambda req: False)
    monkeypatch.setattr(pda, "request_owner_persona", lambda req: "kev")
    assert pda.evaluate_protected_data_access(request()).reason == "owner_session_required"

    monkeypatch.setattr(pda, "owner_session_authorized", lambda req: True)
    monkeypatch.setattr(pda, "request_owner_persona", lambda req: None)
    assert pda.evaluate_protected_data_access(request()).reason == "owner_persona_required"

    monkeypatch.setattr(pda, "request_owner_persona", lambda req: "kev")
    assert pda.evaluate_protected_data_access(request()).allowed is True


def test_principal_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("AURA_SEC_PROTECTED_DATA_PERSONAS", raising=False)
    monkeypatch.setattr(pda, "owner_session_authorized", lambda req: True)
    monkeypatch.setattr(pda, "request_owner_persona", lambda req: "kev")
    assert pda.evaluate_protected_data_access(request()).allowed is False


def test_audit_is_required_for_success(monkeypatch):
    monkeypatch.setenv("AURA_SEC_PROTECTED_DATA_PERSONAS", "mary")
    monkeypatch.setattr(pda, "owner_session_authorized", lambda req: True)
    monkeypatch.setattr(pda, "request_owner_persona", lambda req: "mary")

    ledger = Audit()
    decision = pda.authorize_protected_data_access(
        request(), action="owner_user_detail_read", subject_user_id="opaque-id", audit=ledger
    )
    assert decision.allowed is True
    assert ledger.entries[0]["action"] == "protected_data_access_granted"
    assert ledger.entries[0]["subject_user_id"] == "opaque-id"

    denied = pda.authorize_protected_data_access(
        request(), action="owner_user_detail_read", audit=Audit(fail=True)
    )
    assert denied.allowed is False
    assert denied.reason == "audit_unavailable"


def test_protected_drive_target_is_exact_match(monkeypatch):
    monkeypatch.setenv("AURA_SEC_PROTECTED_DATA_DRIVE_FOLDER_ID", "approved-folder")
    assert pda.approved_protected_record_target(provider="google_drive", target_id="approved-folder")
    assert not pda.approved_protected_record_target(provider="google_drive", target_id="other-folder")
    assert not pda.approved_protected_record_target(provider="local", target_id="approved-folder")
