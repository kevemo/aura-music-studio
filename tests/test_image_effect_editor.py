from aura_music_studio.access_control import BASIC_CREATE, _required_feature
from aura_music_studio.image_effect_editor import image_effect_editor


def test_image_effect_editor_uses_existing_basic_create_entitlement() -> None:
    assert _required_feature("/image-effects/editor", "GET") == BASIC_CREATE


def test_editor_is_same_origin_and_wires_exact_preview_save_contract() -> None:
    response = image_effect_editor()
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "eval(" not in body
    assert "innerHTML" not in body
    assert "/image-effects/compose" in body
    assert "/image-effects/preview" in body
    assert "/image-effects/presets/" in body
    assert "preview_token" in body
    assert "credentials:'same-origin'" in body


def test_canonical_app_mounts_image_effect_editor_once() -> None:
    from aura_music_studio.api import app

    count = 0
    for route in app.routes:
        if getattr(route, "path", "") != "/image-effects/editor":
            continue
        if "GET" in (getattr(route, "methods", set()) or set()):
            count += 1

    assert count == 1
