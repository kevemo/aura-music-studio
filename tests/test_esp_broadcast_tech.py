from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_broadcast_tech import (
    BroadcastProfileRequest,
    BroadcastTechStore,
    ChecklistRequest,
    NetworkTestRequest,
    _reject_secrets,
    router,
)
from aura_music_studio.esp_command_center import EspStore


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("broadcast@example.com", "Broadcast Creator", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return user, BroadcastTechStore(EspStore(accounts))


def test_broadcast_profile_requires_consent_to_retain_setup_metadata(tmp_path):
    user, store = _setup(tmp_path)
    saved = store.save_profile(
        user["id"],
        BroadcastProfileRequest(
            setup_type="live_studio",
            operating_system="Windows 11",
            microphone="USB microphone",
            consent_store_setup=False,
        ),
    )
    assert saved["consent_store_setup"] is False
    assert saved["setup_type"] == "other"
    assert saved["microphone"] == ""

    saved = store.save_profile(
        user["id"],
        BroadcastProfileRequest(
            setup_type="live_studio",
            operating_system="Windows 11",
            microphone="USB microphone",
            consent_store_setup=True,
        ),
    )
    assert saved["consent_store_setup"] is True
    assert saved["setup_type"] == "live_studio"
    assert saved["microphone"] == "USB microphone"


def test_broadcast_tech_rejects_secret_like_notes():
    with pytest.raises(ValueError, match="Do not store passwords"):
        _reject_secrets("password=do-not-save-this")
    with pytest.raises(ValueError, match="Do not store passwords"):
        _reject_secrets("stream key: abc123")
    assert _reject_secrets("USB microphone into interface input 1") == "USB microphone into interface input 1"


def test_checklist_and_network_diagnostics_feed_explainable_readiness(tmp_path):
    user, store = _setup(tmp_path)
    first = store.checklist(user["id"])[0]
    store.set_checklist(user["id"], first["key"], state="needs_help", note="Connection drops occasionally")
    store.add_network_test(
        user["id"],
        NetworkTestRequest(upload_mbps=3.5, download_mbps=80, latency_ms=30, packet_loss_percent=3.2),
    )
    readiness = store.readiness(user["id"])
    assert readiness["state"] == "needs_help"
    assert readiness["diagnostic_only"] is True
    assert readiness["not_platform_requirement"] is True
    assert readiness["remote_control_enabled"] is False
    assert readiness["credentials_stored"] is False
    assert any("packet loss" in reason for reason in readiness["reasons"])


def test_network_test_is_member_reported_not_claimed_as_device_measurement(tmp_path):
    user, store = _setup(tmp_path)
    row = store.add_network_test(user["id"], NetworkTestRequest(upload_mbps=20, latency_ms=15))
    assert row["source"] == "member_reported"


def test_broadcast_tech_routes_cover_profile_checklist_and_network_records():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/broadcast-tech" in paths
    assert "/command-center/api/broadcast-tech" in paths
    assert "/command-center/api/broadcast-tech/profile" in paths
    assert "/command-center/api/broadcast-tech/checklist/{item_key}" in paths
    assert "/command-center/api/broadcast-tech/network-tests" in paths
