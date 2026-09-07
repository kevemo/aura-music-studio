from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_performance_compensation import (
    AgentPerformanceStore,
    BonusRule,
    CompensationReviewRequest,
    CompensationStatusRequest,
    PerformanceSubmission,
    RuleSetCreate,
    router,
)
from aura_music_studio.esp_command_center import EspStore


def _agent(tmp_path, region="UK+"):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    signup = accounts.signup("agent@example.com", "ESP Agent", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], "agent", "agent", region, "test")
    esp.decide(token, "approve", "agent", "Owner")
    return accounts, esp, user, AgentPerformanceStore(esp.db_path)


def _rules(**overrides):
    data = {
        "name": "Current Agent Programme",
        "region": "global",
        "effective_from": "2026-08",
        "effective_to": None,
        "metric_targets": {"apply_links": 100, "recruitment_videos": 10, "new_creators": 5},
        "minimum_completion_percent": 70,
        "base_commission_percent": 25,
        "below_minimum_commission_percent": 12.5,
        "bonus_rules": [
            BonusRule(metric="new_creators", threshold=10, bonus_percent=10, label="Recruitment growth")
        ],
        "max_commission_percent": 50,
        "currency": "GBP",
        "notes": "Test rules only; production values remain owner-configurable.",
    }
    data.update(overrides)
    return RuleSetCreate(**data)


def test_rules_are_effective_dated_and_region_specific(tmp_path):
    _accounts, _esp, agent, store = _agent(tmp_path, region="UK+")
    global_rules = store.create_ruleset("Mary", _rules(name="Global", region="global"))
    uk_rules = store.create_ruleset(
        "Kev",
        _rules(
            name="UK+ August",
            region="UK+",
            effective_from="2026-08",
            effective_to="2026-08",
            base_commission_percent=30,
            max_commission_percent=55,
        ),
    )
    august = store.active_ruleset(agent["id"], "2026-08")
    september = store.active_ruleset(agent["id"], "2026-09")
    assert august["id"] == uk_rules["id"]
    assert august["base_commission_percent"] == 30
    assert september["id"] == global_rules["id"]
    assert september["base_commission_percent"] == 25


def test_calculation_is_explainable_and_bonus_is_capped():
    rules = _rules(
        metric_targets={"apply_links": 100, "new_creators": 5},
        minimum_completion_percent=70,
        base_commission_percent=25,
        below_minimum_commission_percent=12.5,
        bonus_rules=[BonusRule(metric="new_creators", threshold=10, bonus_percent=40, label="High recruitment")],
        max_commission_percent=50,
    ).model_dump()
    result = AgentPerformanceStore.calculate(
        {"apply_links": 100, "new_creators": 10}, rules, eligible_base_minor=100_00
    )
    assert result["overall_completion_percent"] == 100.0
    assert result["meets_configured_minimum"] is True
    assert result["metric_components"][0]["met"] is True
    assert result["bonus_rules"][0]["earned"] is True
    assert result["calculated_commission_percent"] == 50.0
    assert result["calculated_amount_minor"] == 5000
    assert result["automatic_penalty"] is False
    assert result["automatic_payout"] is False
    assert result["owner_approval_required"] is True


def test_below_minimum_uses_configured_reduced_rate_not_a_penalty(tmp_path):
    _accounts, _esp, agent, store = _agent(tmp_path)
    rules = store.create_ruleset("Mary", _rules())
    performance = store.save_period(
        agent["id"], "2026-08",
        PerformanceSubmission(
            metrics={"apply_links": 20, "recruitment_videos": 2, "new_creators": 1},
            evidence_note="Draft evidence",
            submit_for_review=False,
        ),
    )
    preview = store.preview(agent["id"], "2026-08", eligible_base_minor=20_000)
    assert preview["ruleset"]["id"] == rules["id"]
    assert preview["performance"]["id"] == performance["id"]
    assert preview["calculation"]["meets_configured_minimum"] is False
    assert preview["calculation"]["calculated_commission_percent"] == 12.5
    assert preview["calculation"]["automatic_penalty"] is False


def test_submission_requires_evidence_note_and_owner_verifies_base(tmp_path):
    _accounts, _esp, agent, store = _agent(tmp_path)
    store.create_ruleset("Mary", _rules())
    with pytest.raises(ValueError, match="evidence note"):
        store.save_period(
            agent["id"], "2026-08",
            PerformanceSubmission(
                metrics={"apply_links": 100, "recruitment_videos": 10, "new_creators": 5},
                evidence_note="",
                submit_for_review=True,
            ),
        )

    submitted = store.save_period(
        agent["id"], "2026-08",
        PerformanceSubmission(
            metrics={"apply_links": 100, "recruitment_videos": 10, "new_creators": 5},
            evidence_note="Reviewed activity log and recruitment records uploaded for owner review.",
            submit_for_review=True,
        ),
    )
    assert submitted["status"] == "submitted"

    review = store.approve_review(
        "Kev",
        CompensationReviewRequest(
            agent_user_id=agent["id"],
            period="2026-08",
            verified_eligible_base_minor=40_000,
            currency="GBP",
            note="Eligible base verified by owner before approval.",
        ),
    )
    assert review["status"] == "approved"
    assert review["verified_eligible_base_minor"] == 40_000
    assert review["calculated_percent"] == 25
    assert review["calculated_amount_minor"] == 10_000
    assert review["calculation"]["automatic_payout"] is False
    assert store.performance_period(agent["id"], "2026-08")["status"] == "owner_reviewed"

    with pytest.raises(ValueError, match="locked"):
        store.save_period(
            agent["id"], "2026-08",
            PerformanceSubmission(metrics={"apply_links": 999}, evidence_note="change", submit_for_review=False),
        )


def test_paid_status_is_manual_record_only_and_void_is_terminal(tmp_path):
    _accounts, _esp, agent, store = _agent(tmp_path)
    store.create_ruleset("Mary", _rules())
    store.save_period(
        agent["id"], "2026-08",
        PerformanceSubmission(
            metrics={"apply_links": 100, "recruitment_videos": 10, "new_creators": 5},
            evidence_note="Evidence supplied.",
            submit_for_review=True,
        ),
    )
    review = store.approve_review(
        "Mary",
        CompensationReviewRequest(
            agent_user_id=agent["id"], period="2026-08",
            verified_eligible_base_minor=10_000, currency="GBP",
        ),
    )
    paid = store.set_review_status(
        "Mary", review["id"],
        CompensationStatusRequest(status="paid", note="Recorded after external payment confirmation.", payment_reference="manual-ref-1"),
    )
    assert paid["status"] == "paid"
    assert paid["payment_reference"] == "manual-ref-1"
    assert paid["paid_at"] is not None
    dashboard = store.owner_dashboard()
    assert dashboard["money_transfer_performed"] is False
    assert dashboard["automatic_penalties"] is False

    voided = store.set_review_status(
        "Kev", paid["id"], CompensationStatusRequest(status="void", note="Administrative correction"),
    )
    assert voided["status"] == "void"
    with pytest.raises(ValueError, match="cannot be changed"):
        store.set_review_status("Kev", paid["id"], CompensationStatusRequest(status="approved"))


def test_no_ruleset_returns_truthful_no_calculation(tmp_path):
    _accounts, _esp, agent, store = _agent(tmp_path)
    store.save_period(
        agent["id"], "2026-08",
        PerformanceSubmission(metrics={"apply_links": 10}, evidence_note="Draft evidence"),
    )
    preview = store.preview(agent["id"], "2026-08")
    assert preview["ruleset"] is None
    assert preview["calculation"] is None
    assert "No active owner-configured" in preview["message"]


def test_performance_router_exposes_agent_and_owner_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/agent/performance" in paths
    assert "/command-center/api/agent/performance/{period}" in paths
    assert "/command-center/agent/performance" in paths
    assert "/owner/api/esp-agent-performance" in paths
    assert "/owner/api/esp-agent-performance/rulesets" in paths
    assert "/owner/api/esp-agent-performance/reviews" in paths
    assert "/owner/api/esp-agent-performance/reviews/{review_id}" in paths
    assert "/owner/esp-agent-performance" in paths
