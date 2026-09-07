from __future__ import annotations


def test_canonical_production_entrypoint_mounts_scoped_relay_identity_boundary():
    import app as production_entrypoint

    from aura_music_studio.aura_live_relay_error_boundary import AuraLiveRelayErrorBoundaryMiddleware
    from aura_music_studio.aura_live_relay_identity import AuraLiveRelayIdentityMiddleware
    from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware

    middleware = [entry.cls for entry in production_entrypoint.app.user_middleware]
    assert middleware.count(AuraLiveRelayIdentityMiddleware) == 1
    assert middleware.count(AuraLiveRelayErrorBoundaryMiddleware) == 1
    assert middleware.count(CrossSiteRequestGuardMiddleware) == 1

    # FastAPI stores newly-added middleware first. The browser request guard must remain outermost;
    # bearer relay requests are explicitly exempt there, then the relay-only HTTP error boundary
    # catches identity validation failures before the scoped identity middleware forwards traffic.
    assert middleware.index(CrossSiteRequestGuardMiddleware) < middleware.index(AuraLiveRelayErrorBoundaryMiddleware)
    assert middleware.index(AuraLiveRelayErrorBoundaryMiddleware) < middleware.index(AuraLiveRelayIdentityMiddleware)
