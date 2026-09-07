from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_incentives import (
    ClaimRequest,
    IncentiveStore,
    OwnerReview,
    evaluate_maintenance,
    router,
)


def _user(accounts: AccountStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def base_metrics():
    return {"region": "UK+", "diamonds": 250000, "live_days": 18, "live_hours": 65, "joined_referrals": 1}


def base_checklist():
    return {
        "maintained_previous_month": True,
        "discord_active": True,
        "two_daily_videos": True,
        "prelive_video": True,
        "tiktok_bonus_activated": True,
        "network_funds_received": True,
    }


def test_maintenance_base_returns_only_five_percent_with_verified_funding():
    result = evaluate_maintenance(base_metrics(), base_checklist())
    assert result["base_requirements_met"] is True
    assert result["enhanced_requirements_met"] is False
    assert result["tiktok_bonus_and_network_funds_verified"] is True
    assert result["provisional_percentage"] == 5
    assert result["owner_review_required"] is True
    assert result["award_automatic"] is False


def test_maintenance_enhanced_returns_ten_not_an_intermediate_percentage():
    metrics = {
        "region": "UK+",
        "diamonds": 300000,
        "live_days": 24,
        "live_hours": 90,
        "joined_referrals": 2,
        "scheduled_battles": 4,
    }
    checklist = {
        "maintained_previous_month": True,
        "required_meetings": True,
        "analytics_training": True,
        "discord_active": True,
        "campaign_participation": True,
        "enhanced_video_strategy": True,
        "professional_good_standing": True,
        "two_daily_videos": True,
        "prelive_video": True,
        "tiktok_bonus_activated": True,
        "network_funds_received": True,
    }
    result = evaluate_maintenance(metrics, checklist)
    assert result["enhanced_requirements_met"] is True
    assert result["provisional_percentage"] == 10
    assert result["provisional_percentage"] in {0, 5, 10}


def test_requirements_without_provider_activation_or_received_funds_never_create_payment_eligibility():
    checklist = base_checklist()
    checklist["network_funds_received"] = False
    result = evaluate_maintenance(base_metrics(), checklist)
    assert result["base_requirements_met"] is True
    assert result["tiktok_bonus_and_network_funds_verified"] is False
    assert result["provisional_percentage"] == 0
    assert result["award_automatic"] is False


def test_latam_spain_uses_documented_lower_maintenance_threshold():
    metrics = base_metrics()
    metrics.update({"region": "LATAM", "diamonds": 90000})
    result = evaluate_maintenance(metrics, base_checklist())
    assert result["regional_threshold"] == 80000
    assert result["base_requirements"]["maintenance_threshold"] is True


def test_claim_is_user_scoped_and_owner_review_is_explicit(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    creator = _user(accounts, "reward-creator@example.com")
    other = _user(accounts, "other-creator@example.com")
    store = IncentiveStore(accounts.db_path)
    claim = store.create(
        creator["id"],
        ClaimRequest(
            program_id="maintenance",
            period_label="August 2026",
            metrics=base_metrics(),
            checklist=base_checklist(),
            note="Submitted for human verification.",
        ),
    )
    assert claim["status"] == "tracking"
    assert claim["evaluation"]["provisional_percentage"] == 5
    assert claim["evaluation"]["owner_review_required"] is True

    try:
        store.get(claim["id"], user_id=other["id"])
        assert False, "Another creator must not read the claim"
    except KeyError:
        pass

    reviewed = store.review(
        claim["id"],
        OwnerReview(status="qualified", owner_reason="Evidence and provider funding verified.", reward_record="Verified pending issue"),
    )
    assert reviewed["status"] == "qualified"
    assert "verified" in reviewed["owner_reason"].lower()


def test_duplicate_program_period_is_blocked(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    creator = _user(accounts, "duplicate-reward@example.com")
    store = IncentiveStore(accounts.db_path)
    body = ClaimRequest(program_id="consistency-train", period_label="2026-Q3")
    store.create(creator["id"], body)
    try:
        store.create(creator["id"], body)
        assert False, "Duplicate programme/period must be rejected"
    except FileExistsError:
        pass


def test_incentive_routes_live_only_under_private_esp_namespace():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/incentives" in paths
    assert "/command-center/api/incentives/programs" in paths
    assert "/command-center/api/incentives/claims" in paths
    assert "/command-center/api/incentives/claims/{claim_id}/owner-review" in paths
    assert "/incentives" not in paths
