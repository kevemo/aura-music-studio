from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import EconomyError, EligibilityDecision, LiveGiftContext
from aura_music_studio.cosmic_economy_api import router
from aura_music_studio.cosmic_economy_integrations import economy_service
from aura_music_studio.cosmic_economy_personal_limits import PersonalLimitCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def economy(tmp_path):
    return PersonalLimitCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=Live(),
        eligibility=AllowEligibility(),
    )


def send(e, key):
    return e.send_gift(
        sender_user_id="viewer-1",
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key=key,
    )


def test_canonical_economy_service_includes_personal_limit_layer(tmp_path):
    service = economy_service(tmp_path / "canonical.sqlite3")
    assert isinstance(service, PersonalLimitCosmicEconomy)


def test_personal_limit_can_only_be_lower_than_platform_limit(tmp_path):
    e = economy(tmp_path)
    e.set_spending_limits(
        "viewer-1",
        actor="owner-test",
        reason="platform safety cap",
        daily_hard_limit=100,
    )
    with pytest.raises(EconomyError) as exc:
        e.set_personal_spending_limits("viewer-1", daily_hard_limit=150)
    assert exc.value.code == "PERSONAL_LIMIT_EXCEEDS_PLATFORM_LIMIT"
    assert exc.value.status_code == 409

    personal = e.set_personal_spending_limits("viewer-1", daily_hard_limit=50)
    assert personal["daily_hard_limit"] == 50
    state = e.spending_state("viewer-1")
    assert state["effective_hard_limits"]["daily"] == 50
    assert state["remaining_hard_limit"]["daily"] == 50

    history = e.personal_spending_limit_history("viewer-1")
    assert len(history) == 1
    assert history[0]["previous"] == {}
    assert history[0]["new"]["daily_hard_limit"] == 50
    events = e.pending_outbox(limit=20)
    assert any(row["event_type"] == "economy.personal_spending_limits_changed" for row in events)


def test_personal_limit_blocks_gift_atomically_without_consuming_coins(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    e.set_personal_spending_limits("viewer-1", daily_hard_limit=20)

    send(e, "gift-1")
    send(e, "gift-2")
    assert e.get_balance("viewer-1")["available_coins"] == 80

    with pytest.raises(EconomyError) as exc:
        send(e, "gift-3")
    assert exc.value.code == "PERSONAL_SPENDING_LIMIT_EXCEEDED"
    assert exc.value.details["period"] == "daily"
    assert e.get_balance("viewer-1")["available_coins"] == 80

    state = e.spending_state("viewer-1")
    assert state["spent"]["daily"] == 20
    assert state["remaining_hard_limit"]["daily"] == 0
    blocked = e.operational_events(
        event_type="economy.personal_spending_limit_blocked",
        user_id="viewer-1",
    )
    assert len(blocked) == 1
    assert blocked[0]["details"]["spent_coins"] == 20
    assert blocked[0]["details"]["attempted_coins"] == 10


def test_platform_limit_remains_when_personal_limit_is_cleared(tmp_path):
    e = economy(tmp_path)
    e.set_spending_limits(
        "viewer-1",
        actor="owner-test",
        reason="platform safety cap",
        daily_hard_limit=30,
    )
    e.set_personal_spending_limits("viewer-1", daily_hard_limit=20)
    cleared = e.set_personal_spending_limits(
        "viewer-1",
        daily_hard_limit=None,
        weekly_hard_limit=None,
        monthly_hard_limit=None,
    )
    assert cleared["daily_hard_limit"] is None
    state = e.spending_state("viewer-1")
    assert state["effective_hard_limits"]["daily"] == 30
    history = e.personal_spending_limit_history("viewer-1")
    assert len(history) == 2
    assert history[0]["previous"]["daily_hard_limit"] == 20
    assert history[0]["new"]["daily_hard_limit"] is None


def test_personal_spending_limit_route_is_member_scoped_surface():
    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()))))
        for route in router.routes
    }
    assert ("/economy/me/personal-spending-limits", ("PUT",)) in routes
