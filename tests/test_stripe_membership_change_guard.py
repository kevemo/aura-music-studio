from __future__ import annotations

import pytest

from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.stripe_membership_checkout import approved_checkout_period


@pytest.mark.parametrize(
    ("current_plan", "requested_plan", "requested_period"),
    [
        ("base", "base", BillingPeriod.MONTHLY),
        ("base", "pro", BillingPeriod.MONTHLY),
        ("base", "pro", BillingPeriod.ANNUAL),
        ("pro", "base", BillingPeriod.MONTHLY),
        ("pro", "pro", BillingPeriod.MONTHLY),
        ("pro", "pro", BillingPeriod.ANNUAL),
    ],
)
def test_active_paid_membership_cannot_start_second_subscription_checkout(
    current_plan: str,
    requested_plan: str,
    requested_period: BillingPeriod,
):
    user = {"id": "active_paid_user", "status": "active", "plan_id": current_plan}

    with pytest.raises(ValueError, match="cannot create a second Stripe subscription Checkout session"):
        approved_checkout_period(user, requested_plan, requested_period)


def test_active_free_account_is_not_misclassified_as_existing_paid_subscription():
    user = {"id": "active_free_user", "status": "active", "plan_id": "free"}

    assert approved_checkout_period(user, "base", BillingPeriod.MONTHLY) is BillingPeriod.MONTHLY


def test_basic_annual_still_fails_at_canonical_catalogue_before_lifecycle_logic():
    user = {"id": "active_free_user", "status": "active", "plan_id": "free"}

    with pytest.raises(ValueError, match="Annual billing is not available"):
        approved_checkout_period(user, "base", BillingPeriod.ANNUAL)
