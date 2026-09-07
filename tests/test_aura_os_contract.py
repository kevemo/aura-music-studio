from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_os_contract import AuraOsEnvelope, manifest, validate_action


def _time(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def test_manifest_has_no_generic_shell_or_script_execution():
    security = manifest()["security"]
    assert security["generic_shell"] is False
    assert security["generic_powershell"] is False
    assert security["generic_script_execution"] is False
    assert security["arbitrary_filesystem_paths"] is False


def test_unknown_or_command_like_action_is_rejected():
    with pytest.raises(ValueError):
        validate_action("shell.exec", "desktop_overlay", {"command": "whoami"})
    with pytest.raises(ValueError):
        validate_action("unknown.action", "desktop_overlay", {})


def test_media_action_accepts_opaque_asset_id_not_filesystem_path():
    clean = validate_action("media.play_approved", "desktop_overlay", {"asset_id": "asset_123", "channel": "preview"})
    assert clean["asset_id"] == "asset_123"
    with pytest.raises(ValueError, match="opaque"):
        validate_action("media.play_approved", "desktop_overlay", {"asset_id": "../../secret.wav", "channel": "preview"})


def test_external_navigation_requires_https_without_credentials():
    assert validate_action("browser.open_https", "desktop_overlay", {"url": "https://example.com/path"})["url"] == "https://example.com/path"
    with pytest.raises(ValueError):
        validate_action("browser.open_https", "desktop_overlay", {"url": "http://example.com"})
    with pytest.raises(ValueError):
        validate_action("browser.open_https", "desktop_overlay", {"url": "https://user:pass@example.com"})


def test_envelope_is_short_lived_and_digest_is_deterministic():
    envelope = AuraOsEnvelope(
        request_id="request_1234567890",
        surface="desktop_overlay",
        action_id="notification.show",
        parameters={"title": "Aura", "body": "Render complete"},
        issued_at=_time(0),
        expires_at=_time(60),
        actor_id="user_1",
        session_id="session_1",
    )
    assert len(envelope.digest()) == 64
    assert envelope.digest() == envelope.digest()

    with pytest.raises(ValueError, match="two minutes"):
        AuraOsEnvelope(
            request_id="request_1234567890",
            surface="desktop_overlay",
            action_id="notification.show",
            parameters={"title": "Aura", "body": "Render complete"},
            issued_at=_time(0),
            expires_at=_time(121),
            actor_id="user_1",
            session_id="session_1",
        )


def test_strong_reauth_action_requires_approval_evidence():
    with pytest.raises(ValueError, match="approval evidence"):
        AuraOsEnvelope(
            request_id="request_1234567890",
            surface="desktop_overlay",
            action_id="workflow.approve",
            parameters={"approval_id": "approval_1"},
            issued_at=_time(0),
            expires_at=_time(60),
            actor_id="user_1",
            session_id="session_1",
        )

    envelope = AuraOsEnvelope(
        request_id="request_1234567890",
        surface="desktop_overlay",
        action_id="workflow.approve",
        parameters={"approval_id": "approval_1"},
        issued_at=_time(0),
        expires_at=_time(60),
        actor_id="user_1",
        session_id="session_1",
        approval_evidence_id="evidence_1",
    )
    assert envelope.action_id == "workflow.approve"


def test_native_security_action_is_not_available_to_general_overlay():
    with pytest.raises(ValueError, match="not allowed on this surface"):
        validate_action("aura_sec.poll_internal", "desktop_overlay", {"device_id": "device_1"})
    assert validate_action("aura_sec.poll_internal", "native_security_client", {"device_id": "device_1"})["device_id"] == "device_1"
