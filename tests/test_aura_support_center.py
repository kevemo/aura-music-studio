from __future__ import annotations

import inspect
import sqlite3

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_support_center import SupportStore, router


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "support.sqlite3")
    return accounts, SupportStore(accounts)


def test_aura_support_explains_major_site_features(tmp_path):
    _, store = _store(tmp_path)
    answer = store.answer("How do I use stems and the professional DAW?")
    assert answer["matched_feature"] == "music"
    assert "Professional DAW" in answer["answer"]
    assert answer["confidence"] >= 0.55


def test_support_ticket_escalates_sensitive_requests(tmp_path):
    _, store = _store(tmp_path)
    ticket = store.create_ticket(
        email="member@example.com",
        display_name="Member",
        subject="Payment failed",
        message="I was charged but my subscription has not activated.",
        category="billing",
    )
    assert ticket["escalated"] == 1
    result = store.process_aura_response(ticket["id"], allow_auto_send=False)
    assert result["requires_owner"] is True
    assert "Mary or Kev" in result["answer"]


def test_general_feature_question_can_receive_bounded_aura_answer(tmp_path):
    _, store = _store(tmp_path)
    ticket = store.create_ticket(
        email="member@example.com",
        display_name="Member",
        subject="Video editor help",
        message="How do I use the timeline and captions in the video editor?",
    )
    result = store.process_aura_response(ticket["id"], allow_auto_send=False)
    assert result["requires_owner"] is False
    assert result["matched_feature"] == "video"


def test_support_email_external_reference_is_idempotent(tmp_path):
    _, store = _store(tmp_path)
    store.create_ticket(
        email="member@example.com",
        display_name="Member",
        subject="Music help",
        message="How do I master a song?",
        external_message_ref="mail-123",
    )
    try:
        store.create_ticket(
            email="member@example.com",
            display_name="Member",
            subject="Duplicate",
            message="This is the same inbound email.",
            external_message_ref="mail-123",
        )
        assert False, "duplicate external message ref should fail"
    except sqlite3.IntegrityError:
        pass


def test_support_routes_exist_and_are_composed_into_base_api():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/help-support" in paths
    assert "/support/contact" in paths
    assert "/support/inbound-email" in paths
    assert "/owner/support" in paths

    import aura_music_studio.api as aggregate
    source = inspect.getsource(aggregate)
    assert "aura_support_center_router" in source
    assert "app.include_router(aura_support_center_router)" in source
