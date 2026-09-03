from __future__ import annotations

from aura_music_studio.commercial_catalogue import public_commercial_catalogue
from aura_music_studio.plans import get_plan


def test_public_catalogue_uses_basic_name_without_changing_internal_plan_identity():
    catalogue = public_commercial_catalogue()
    memberships = {item["id"]: item for item in catalogue["memberships"]}

    assert memberships["free"]["name"] == "Free"
    assert memberships["base"]["name"] == "Basic"
    assert memberships["pro"]["name"] == "Unlimited Pro"
    assert memberships["base"]["monthly_price"] == "4.99"
    assert memberships["pro"]["monthly_price"] == "9.99"
    assert memberships["pro"]["annual_price"] == "99.00"

    # Compatibility boundary: the stable internal plan identity and historical name remain
    # untouched so existing billing/session/test contracts cannot be silently reinterpreted.
    internal = get_plan("base")
    assert internal.id == "base"
    assert internal.name == "Member"


def test_public_catalogue_rewrites_retired_member_copy_only_in_projection():
    catalogue = public_commercial_catalogue()
    memberships = {item["id"]: item for item in catalogue["memberships"]}

    assert "unlock on Basic" in memberships["free"]["description"]
    assert "£4.99 Basic tier" in memberships["base"]["description"]
    assert "unlock on Member" not in memberships["free"]["description"]
    assert "£4.99 Member tier" not in memberships["base"]["description"]

    # Source Plan objects remain unchanged; presentation normalization must not mutate them.
    assert "£4.99 Member tier" in get_plan("base").description
