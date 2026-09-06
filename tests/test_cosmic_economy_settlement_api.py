from __future__ import annotations

from aura_music_studio.cosmic_economy_owner_api import router


def test_owner_settlement_reconciliation_routes_are_registered():
    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()))))
        for route in router.routes
    }
    assert ("/owner/economy/settlement-reconciliation/{provider_name}", ("POST",)) in routes
    assert (
        "/owner/economy/settlement-reconciliation/{provider_name}/purchases/{purchase_id}",
        ("POST",),
    ) in routes
