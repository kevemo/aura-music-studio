from __future__ import annotations

from aura_music_studio.commercial_catalogue import public_commercial_catalogue
from aura_music_studio.plans import get_plan


def test_public_catalogue_uses_basic_name_without_changing_internal_plan_identity():
    catalogue = public_commercial_catalogue()
    memberships = {item["id"]: item for item in catalogue["memberships"]}

    assert memberships["free"]["name"] == "Free"
    assert memberships["base"]["name"] == "Basic"
    assert memberships["pro"]["name"] == "Unlimited Pro"
    assert memberships["base"]["monthly_price"] == "5.99"
    assert memberships["base"]["annual_price"] == "59.99"
    assert memberships["pro"]["monthly_price"] == "9.99"
    assert memberships["pro"]["annual_price"] == "99.00"

    # Compatibility boundary: the stable internal plan ID and historical name remain
    # untouched even though public naming and commercial prices evolve.
    internal = get_plan("base")
    assert internal.id == "base"
    assert internal.name == "Member"


def test_public_catalogue_uses_plan_owned_commercial_copy_without_price_rewrites():
    catalogue = public_commercial_catalogue()
    memberships = {item["id"]: item for item in catalogue["memberships"]}

    assert "unlock on Basic" in memberships["free"]["description"]
    assert "£5.99/month or £59.99/year Basic tier" in memberships["base"]["description"]
    assert "unlock on Member" not in memberships["free"]["description"]
    assert "£4.99" not in memberships["base"]["description"]

    # Price/copy authority is plans.py; the public projection does not mutate or rewrite it.
    assert memberships["base"]["description"] == get_plan("base").description
