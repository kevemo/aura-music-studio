from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_store import (
    AuraSecCommandStore,
    VerifiedCommandReceiptSignature,
    canonical_command_receipt_payload,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, CommandReceipt
from aura_music_studio.aura_sec_store import AuraSecStore


DEVICE_FP = "9" * 64
SIG_A = base64.b64encode(b"a" * 64).decode("ascii")
SIG_B = base64.b64encode(b"b" * 64).decode("ascii")


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "receipt-idempotency.sqlite3")
    signup = accounts.signup(
        "receipt.retry@example.test",
        "Receipt Retry Member",
        "secure-receipt-retry-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-receipt-idempotency",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Receipt Retry PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint=DEVICE_FP,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details={"fixture": True},
    )
    security.approve_action(signup.user_id, action["id"])
    commands = AuraSecCommandStore(accounts, security)
    command = commands.issue_approved_action(
        signup.user_id,
        action["id"],
        policy_version="policy-receipt-idempotency",
        nonce="receipt-idempotency-nonce-0001",
        parameters={"object_id": "quarantine-object-idempotency"},
        ttl_seconds=30,
    )
    return security, commands, signup.user_id, device, action, command


def _verifier(expected_fingerprint, payload, signature):
    assert expected_fingerprint == DEVICE_FP
    assert signature in {b"a" * 64, b"b" * 64}
    return VerifiedCommandReceiptSignature(
        public_key_fingerprint=DEVICE_FP,
        verifier_id="receipt-idempotency-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def _accept(commands, user_id, receipt, *, signature=SIG_A, now=None):
    return commands.accept_verified_receipt(
        user_id,
        receipt,
        signature_b64=signature,
        signature_verifier=_verifier,
        now=now,
    )


def test_exact_signed_receipt_retry_is_idempotent_even_with_different_valid_signature_bytes(tmp_path):
    _security, commands, user_id, device, _action, command = _setup(tmp_path)
    receipt = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=datetime.now(timezone.utc),
        result_code="accepted",
        detail="native agent accepted command",
    )

    first = _accept(commands, user_id, receipt, signature=SIG_A)
    second = _accept(commands, user_id, receipt, signature=SIG_B)

    assert second == first
    expected_payload_digest = hashlib.sha256(canonical_command_receipt_payload(receipt)).hexdigest()
    assert first["last_receipt_payload_digest"] == expected_payload_digest
    assert first["last_receipt_signature_digest"] == hashlib.sha256(b"a" * 64).hexdigest()
    # The idempotent retry does not rewrite evidence just because another valid ECDSA-style
    # signature representation was used for the same exact canonical receipt payload.
    assert second["last_receipt_signature_digest"] == first["last_receipt_signature_digest"]


def test_same_state_with_different_signed_payload_is_a_conflict_not_an_idempotent_retry(tmp_path):
    _security, commands, user_id, device, _action, command = _setup(tmp_path)
    occurred = datetime.now(timezone.utc)
    original = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=occurred,
        result_code="accepted",
        detail="original native result",
    )
    _accept(commands, user_id, original)
    before = commands.get(user_id, command.command_id)

    conflicting = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=occurred,
        result_code="accepted",
        detail="different signed result",
    )
    with pytest.raises(ValueError, match="Conflicting Aura Sec command receipt"):
        _accept(commands, user_id, conflicting, signature=SIG_B)

    assert commands.get(user_id, command.command_id) == before


def test_exact_execution_retry_does_not_repeat_action_side_effect(tmp_path):
    security, commands, user_id, device, action, command = _setup(tmp_path)
    received_at = datetime.now(timezone.utc)
    received = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=received_at,
        result_code="accepted",
    )
    _accept(commands, user_id, received)

    executed_at = received_at + timedelta(milliseconds=1)
    executed = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="executed",
        occurred_at=executed_at,
        result_code="executed",
        evidence_digest="d" * 64,
    )
    first = _accept(commands, user_id, executed, signature=SIG_A)
    action_after_first = security.get_action(user_id, action["id"])
    second = _accept(commands, user_id, executed, signature=SIG_B)
    action_after_second = security.get_action(user_id, action["id"])

    assert first["status"] == "executed"
    assert second == first
    assert action_after_first == action_after_second
    assert action_after_second["status"] == "executed"
    assert action_after_second["executed_at"] == executed_at.isoformat()


def test_exact_successful_retry_remains_idempotent_after_command_expiry(tmp_path):
    _security, commands, user_id, device, _action, command = _setup(tmp_path)
    occurred = command.issued_at + timedelta(seconds=1)
    received = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=occurred,
        result_code="accepted",
    )
    first = _accept(commands, user_id, received, now=occurred + timedelta(seconds=1))

    retry_after_expiry = command.expires_at + timedelta(minutes=5)
    second = _accept(
        commands,
        user_id,
        received,
        signature=SIG_B,
        now=retry_after_expiry,
    )
    assert second == first


def test_new_successful_state_after_command_expiry_is_still_rejected(tmp_path):
    _security, commands, user_id, device, _action, command = _setup(tmp_path)
    received_at = command.issued_at + timedelta(seconds=1)
    received = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=received_at,
        result_code="accepted",
    )
    _accept(commands, user_id, received, now=received_at + timedelta(seconds=1))

    executed = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="executed",
        occurred_at=command.expires_at - timedelta(seconds=1),
        result_code="executed",
    )
    with pytest.raises(PermissionError, match="Expired Aura Sec command"):
        _accept(
            commands,
            user_id,
            executed,
            signature=SIG_B,
            now=command.expires_at + timedelta(minutes=1),
        )
    assert commands.get(user_id, command.command_id)["status"] == "received"
