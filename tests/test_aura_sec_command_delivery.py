from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_native_bridge import (
    AuraSecNativeBridge,
    NativeCommandPoll,
    VerifiedNativePollSignature,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


_POLL_SECRET = b"aura-sec-durable-delivery-poll-test-v1"
_SERVER_SIGNER = SelfHostedEd25519CommandSigner(
    Ed25519PrivateKey.generate(),
    key_id="test-durable-server-2026",
)


class _FailingSigner:
    def sign_command(self, command):
        raise RuntimeError("simulated remote HSM outage")


def _setup(tmp_path, *, signer=_SERVER_SIGNER):
    accounts = AccountStore(tmp_path / "aura-sec-durable-delivery.sqlite3")
    signup = accounts.signup(
        "durable.delivery@example.test",
        "Durable Delivery Member",
        "secure-durable-delivery-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-durable-delivery",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Durable Windows PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="a" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details={"command_parameters": {"object_id": "object-durable-001"}},
    )
    security.approve_action(signup.user_id, action["id"])
    commands = AuraSecCommandStore(accounts, security)
    bridge = AuraSecNativeBridge(
        accounts,
        security,
        commands,
        command_signer=signer,
    )
    return accounts, signup.user_id, security, device, action, commands, bridge


def _poll(device_id: str, *, sequence: int, nonce: str, at: datetime | None = None):
    now = at or datetime.now(timezone.utc)
    return NativeCommandPoll(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.2.0",
        policy_version="policy-durable-1",
        session_nonce=nonce,
    )


def _signature_for(poll: NativeCommandPoll) -> str:
    signature = hashlib.sha256(_POLL_SECRET + poll.signed_payload()).digest()
    return base64.b64encode(signature).decode("ascii")


def _verifier(fingerprint: str, payload: bytes, signature: bytes):
    expected = hashlib.sha256(_POLL_SECRET + payload).digest()
    if not secrets.compare_digest(signature, expected):
        return None
    return VerifiedNativePollSignature(
        public_key_fingerprint=fingerprint,
        verifier_id="durable-test-poll-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def _poll_command(bridge, user_id, poll):
    return bridge.poll_verified_command(
        user_id,
        poll,
        signature_b64=_signature_for(poll),
        signature_verifier=_verifier,
    )


def test_lost_transport_response_redelivers_exact_same_signed_envelope(tmp_path):
    _accounts, user_id, _security, device, _action, _commands, bridge = _setup(tmp_path)
    first = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=1, nonce="durable-poll-nonce-0001"),
    )
    second = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=2, nonce="durable-poll-nonce-0002"),
    )

    assert first["command"] == second["command"]
    assert first["command"]["command_id"] == second["command"]["command_id"]
    assert first["command"]["nonce"] == second["command"]["nonce"]
    assert first["command"]["signature_b64"] == second["command"]["signature_b64"]
    state = bridge.deliveries.delivery_state(user_id, first["command"]["command_id"])
    assert state["delivery_count"] == 2
    assert "redelivered unchanged" in second["truth"]


def test_durable_signed_command_survives_bridge_process_restart(tmp_path):
    accounts, user_id, security, device, _action, commands, bridge = _setup(tmp_path)
    first = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=1, nonce="restart-poll-nonce-0001"),
    )

    restarted = AuraSecNativeBridge(
        accounts,
        security,
        commands,
        command_signer=_SERVER_SIGNER,
    )
    second = _poll_command(
        restarted,
        user_id,
        _poll(device["id"], sequence=2, nonce="restart-poll-nonce-0002"),
    )

    assert second["command"] == first["command"]
    state = restarted.deliveries.delivery_state(user_id, first["command"]["command_id"])
    assert state["delivery_count"] == 2


def test_signer_failure_removes_only_never_delivered_command_so_action_can_retry(tmp_path):
    accounts, user_id, _security, device, action, _commands, bridge = _setup(
        tmp_path,
        signer=_FailingSigner(),
    )
    first_poll = _poll(device["id"], sequence=1, nonce="hsm-failure-poll-0001")
    with pytest.raises(PermissionError, match="signing failed closed"):
        _poll_command(bridge, user_id, first_poll)

    with sqlite3.connect(accounts.db_path) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM aura_sec_commands WHERE action_id=?",
            (action["id"],),
        ).fetchone()[0]
    assert count == 0

    bridge.command_signer = _SERVER_SIGNER
    retry = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=2, nonce="hsm-failure-poll-0002"),
    )
    assert retry["command"] is not None
    assert retry["command"]["approval_id"] == action["id"]


def test_corrupted_persisted_signed_envelope_fails_closed_instead_of_redelivery(tmp_path):
    accounts, user_id, _security, device, _action, _commands, bridge = _setup(tmp_path)
    first = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=1, nonce="tamper-poll-nonce-0001"),
    )
    command_id = first["command"]["command_id"]

    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "UPDATE aura_sec_command_deliveries SET signed_envelope_json='{}' WHERE command_id=?",
            (command_id,),
        )

    with pytest.raises(PermissionError, match="signed command envelope is invalid"):
        _poll_command(
            bridge,
            user_id,
            _poll(device["id"], sequence=2, nonce="tamper-poll-nonce-0002"),
        )


def test_expired_signed_envelope_is_never_redelivered(tmp_path):
    _accounts, user_id, _security, device, _action, _commands, bridge = _setup(tmp_path)
    first = _poll_command(
        bridge,
        user_id,
        _poll(device["id"], sequence=1, nonce="expiry-poll-nonce-0001"),
    )
    expires_at = datetime.fromisoformat(first["command"]["expires_at"]).astimezone(timezone.utc)

    assert bridge.deliveries.next_pending_for_device(
        user_id,
        device["id"],
        now=expires_at + timedelta(seconds=1),
    ) is None
    state = bridge.deliveries.delivery_state(user_id, first["command"]["command_id"])
    assert state["delivery_count"] == 1
