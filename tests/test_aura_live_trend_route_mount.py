import app as production_entrypoint


def test_live_trend_coach_api_is_mounted_on_production_entrypoint():
    paths = production_entrypoint.app.openapi().get("paths", {})
    assert "/api/live-show/trends" in paths
