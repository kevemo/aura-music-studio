from types import SimpleNamespace

from starlette.requests import Request

from aura_music_studio.aura_effect_system_portal import effect_system_creator_page


def _request(plan: str = "pro") -> Request:
    scope = {"type": "http", "method": "GET", "path": "/creative/effects-library/create", "headers": []}
    request = Request(scope)
    request.state.member = SimpleNamespace(plan=SimpleNamespace(id=plan))
    return request


def test_effect_system_creator_uses_rhiannon_public_branding():
    html = effect_system_creator_page(_request()).body.decode("utf-8")

    assert "Rhiannon Effect/System Creator" in html
    assert "Rhiannon Intelligence · Editable Effect Systems" in html
    assert "1 · Rhiannon prompt" in html
    assert "Compose with Rhiannon" in html
    assert "Rhiannon turns supported music-effect instructions" in html
    assert "rhiannon.vocal.space" in html
    assert "rhiannon.custom.chain" in html
    assert "Rhiannon composed an editable executable chain" in html
    assert "Compose with Aura" not in html
    assert "1 · Aura prompt" not in html


def test_catalogue_cards_use_canonical_public_metadata_and_do_not_claim_purchase_authority():
    html = effect_system_creator_page(_request()).body.decode("utf-8")

    assert "item.effect_id||item.catalogue_item_id||item.id" in html
    assert "item.label||id" in html
    assert "item.runtime_name||item.runtime" in html
    assert "item.entitlement" in html
    assert "item.ccc_price" in html
    assert "Cosmic Creation Coins" in html
    assert "Ownership is re-checked at preview/apply" in html
    assert "Selecting an item does not grant ownership, preview, apply or execute it." in html
