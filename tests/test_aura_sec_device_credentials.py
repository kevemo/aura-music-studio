from __future__ import annotations

import base64
import hashlib
import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_device_credentials import AuraSecDeviceCredentialRotation, VerifiedCredentialRotation
from aura_music_studio.aura_sec_heartbeat_gateway import AuraSecHeartbeatGateway
from aura_music_studio.aura_sec_native_bridge import AuraSecNativeBridge
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore

OLD_FP = "a" * 64
NEW_FP = "b" * 64
OTHER_FP = "c" * 64
SIG = base64.b64encode(b"s" * 64).decode("ascii")


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "aura-sec-rotation.sqlite3")
    signup = accounts.signup("rotation.member@example.test", "Rotation Member", "a-secure-test-password", "free")
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="test-security-sku",
        payment_reference="rotation-payment-1",
        device_limit=2,
        period_days=31,
        verified_by="test-billing",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Rotation PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint=OLD_FP,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.ROTATE_DEVICE_CREDENTIAL.value,
        risk_class=ActionRisk.STRONG_REAUTH_REQUIRED.value,
        details={"reason": "scheduled key rotation fixture"},
    )
    approved = security.approve_action(signup.user_id, action["id"], strong_reauth_verified=True)
    return accounts, security, AuraSecDeviceCredentialRotation(accounts, security), signup.user_id, device, approved


def _proof(old_fp=OLD_FP, new_fp=NEW_FP, *, hardware=True, digest_override=None):
    def verifier(expected_old, expected_new, payload, old_signature, new_signature):
        assert expected_old == OLD_FP
        assert expected_new == NEW_FP
        assert old_signature == b"s" * 64
        assert new_signature == b"s" * 64
        return VerifiedCredentialRotation(
            old_public_key_fingerprint=old_fp,
            new_public_key_fingerprint=new_fp,
            verifier_id="test-dual-key-verifier",
            old_key_algorithm="ed25519",
            new_key_algorithm="p256",
            evidence_digest=digest_override or hashlib.sha256(payload).hexdigest(),
            new_key_hardware_backed=hardware,
        )
    return verifier


def _db(accounts):
    con = sqlite3.connect(accounts.db_path)
    con.row_factory = sqlite3.Row
    return con


def test_rotation_requires_preapproved_strong_reauth_action(tmp_path):
    accounts = AccountStore(tmp_path / "rotation-policy.sqlite3")
    signup = accounts.signup("rotation.policy@example.test", "Rotation", "test-password-long", "free")
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="test-security-sku",
        payment_reference="rotation-policy-payment",
        device_limit=1,
        period_days=31,
        verified_by="test-billing",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint=OLD_FP,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.ROTATE_DEVICE_CREDENTIAL.value,
        risk_class=ActionRisk.STRONG_REAUTH_REQUIRED.value,
    )
    rotation = AuraSecDeviceCredentialRotation(accounts, security)
    with pytest.raises(PermissionError, match="previously approved"):
        rotation.create_challenge(
            signup.user_id,
            device["id"],
            approved_action_id=action["id"],
            new_public_key_fingerprint=NEW_FP,
        )


def test_rotation_challenge_stores_only_hash_and_supersedes_older_pending(tmp_path):
    accounts, _security, rotation, user_id, device, approved = _setup(tmp_path)
    first = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    second = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    with _db(accounts) as con:
        first_row = con.execute("SELECT challenge_hash,status FROM aura_sec_device_key_rotations WHERE id=?", (first["challenge_id"],)).fetchone()
        second_row = con.execute("SELECT challenge_hash,status FROM aura_sec_device_key_rotations WHERE id=?", (second["challenge_id"],)).fetchone()
    assert first_row["status"] == "superseded"
    assert second_row["status"] == "pending"
    assert first_row["challenge_hash"] == hashlib.sha256(first["challenge"].encode()).hexdigest()
    assert first_row["challenge_hash"] != first["challenge"]
    assert second["member_browser_route_exposed"] is False


def test_missing_verifier_fails_closed_without_burning_valid_challenge(tmp_path):
    accounts, _security, rotation, user_id, device, approved = _setup(tmp_path)
    challenge = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    with pytest.raises(PermissionError, match="trusted Aura Sec credential rotation verifier"):
        rotation.complete_rotation(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            old_signature_b64=SIG,
            new_signature_b64=SIG,
            verifier=None,
        )
    with _db(accounts) as con:
        row = con.execute("SELECT status FROM aura_sec_device_key_rotations WHERE id=?", (challenge["challenge_id"],)).fetchone()
    assert row["status"] == "pending"


def test_rotation_rejects_wrong_new_key_or_evidence_digest(tmp_path):
    _accounts, _security, rotation, user_id, device, approved = _setup(tmp_path)
    challenge = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    with pytest.raises(PermissionError, match="replacement key"):
        rotation.complete_rotation(user_id, challenge["challenge_id"], challenge=challenge["challenge"], old_signature_b64=SIG, new_signature_b64=SIG, verifier=_proof(new_fp=OTHER_FP))
    with pytest.raises(PermissionError, match="evidence digest"):
        rotation.complete_rotation(user_id, challenge["challenge_id"], challenge=challenge["challenge"], old_signature_b64=SIG, new_signature_b64=SIG, verifier=_proof(digest_override="d" * 64))


def test_successful_rotation_invalidates_old_trust_and_requires_fresh_heartbeat(tmp_path):
    accounts, security, rotation, user_id, device, approved = _setup(tmp_path)
    heartbeat_gateway = AuraSecHeartbeatGateway(accounts, security)
    heartbeat_challenge = heartbeat_gateway.issue_challenge(user_id, device["id"])
    AuraSecNativeBridge(accounts, security)
    with _db(accounts) as con:
        con.execute(
            """INSERT INTO aura_sec_native_poll_state
               (device_id,user_id,last_sequence,last_nonce_hash,last_verified_at)
               VALUES (?,?,?,?,datetime('now'))""",
            (device["id"], user_id, 99, "f" * 64),
        )
        con.commit()
    challenge = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    result = rotation.complete_rotation(
        user_id,
        challenge["challenge_id"],
        challenge=challenge["challenge"],
        old_signature_b64=SIG,
        new_signature_b64=SIG,
        verifier=_proof(),
    )
    assert result["rotation_consumed"] is True
    assert result["old_credential_invalidated"] is True
    assert result["fresh_heartbeat_required"] is True
    assert result["action_status"] == "verified"
    assert result["device"]["protection_state"] == "awaiting_heartbeat"
    assert result["device"]["last_seen_at"] is None
    assert result["verification"]["new_key_hardware_backed"] is True
    assert security.get_action(user_id, approved["id"])["status"] == "verified"
    with _db(accounts) as con:
        device_row = con.execute(
            "SELECT public_key_fingerprint,last_heartbeat_sequence,last_heartbeat_verifier FROM aura_sec_devices WHERE user_id=? AND id=?",
            (user_id, device["id"]),
        ).fetchone()
        poll_row = con.execute("SELECT device_id FROM aura_sec_native_poll_state WHERE user_id=? AND device_id=?", (user_id, device["id"])).fetchone()
        heartbeat_row = con.execute("SELECT status FROM aura_sec_heartbeat_challenges WHERE id=?", (heartbeat_challenge["challenge_id"],)).fetchone()
        rotation_row = con.execute("SELECT status,verifier_id,evidence_digest FROM aura_sec_device_key_rotations WHERE id=?", (challenge["challenge_id"],)).fetchone()
    assert device_row["public_key_fingerprint"] == NEW_FP
    assert device_row["last_heartbeat_sequence"] == 0
    assert device_row["last_heartbeat_verifier"] is None
    assert poll_row is None
    assert heartbeat_row["status"] == "superseded"
    assert rotation_row["status"] == "consumed"
    assert rotation_row["verifier_id"] == "test-dual-key-verifier"
    assert len(rotation_row["evidence_digest"]) == 64
    with pytest.raises(PermissionError, match="no longer pending"):
        rotation.complete_rotation(user_id, challenge["challenge_id"], challenge=challenge["challenge"], old_signature_b64=SIG, new_signature_b64=SIG, verifier=_proof())


def test_stale_rotation_challenge_cannot_replace_a_key_changed_elsewhere(tmp_path):
    accounts, _security, rotation, user_id, device, approved = _setup(tmp_path)
    challenge = rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=NEW_FP)
    with _db(accounts) as con:
        con.execute("UPDATE aura_sec_devices SET public_key_fingerprint=? WHERE user_id=? AND id=?", (OTHER_FP, user_id, device["id"]))
        con.commit()
    with pytest.raises(PermissionError, match="changed after this challenge"):
        rotation.complete_rotation(user_id, challenge["challenge_id"], challenge=challenge["challenge"], old_signature_b64=SIG, new_signature_b64=SIG, verifier=_proof())


def test_replacement_key_must_be_unique_and_different(tmp_path):
    _accounts, security, rotation, user_id, device, approved = _setup(tmp_path)
    with pytest.raises(ValueError, match="must differ"):
        rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=OLD_FP)
    security.enroll_attested_device(
        user_id,
        display_name="Other Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint=OTHER_FP,
    )
    with pytest.raises(ValueError, match="already enrolled"):
        rotation.create_challenge(user_id, device["id"], approved_action_id=approved["id"], new_public_key_fingerprint=OTHER_FP)
