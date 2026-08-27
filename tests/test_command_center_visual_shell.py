from pathlib import Path

from starlette.requests import Request

from aura_music_studio import branding
from aura_music_studio.brand_migration import rebrand_text
from aura_music_studio.brand_ui import (
    COMMAND_CENTER_ART_PATH,
    COMMAND_CENTER_MARK_PATH,
    COMMAND_CENTER_THEME_CSS,
)
from aura_music_studio.command_center_visual_shell import apply_visual_shell


def _request(path: str = "/") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"command.example")],
            "client": ("127.0.0.1", 1234),
            "server": ("command.example", 443),
        }
    )


def test_visual_assets_are_current_command_center_assets():
    assert COMMAND_CENTER_MARK_PATH.name == "elevate-souls-command-center-logo.svg"
    assert COMMAND_CENTER_ART_PATH.name == "elevate-souls-command-center-brand.webp"
    assert COMMAND_CENTER_MARK_PATH.exists()
    assert COMMAND_CENTER_ART_PATH.exists()
    assert COMMAND_CENTER_ART_PATH.stat().st_size > 1000
    assert branding.BRAND_MARK_ROUTE == "/brand/command-center-mark.svg"
    assert branding.BRAND_ART_ROUTE == "/brand/command-center-art.webp"


def test_theme_uses_command_center_assets_not_legacy_logo_url():
    assert "/brand/command-center-mark.svg" in COMMAND_CENTER_THEME_CSS
    assert "/brand/esp-logo.webp" not in COMMAND_CENTER_THEME_CSS
    assert "--espcc-gold" in COMMAND_CENTER_THEME_CSS
    assert "prefers-reduced-motion" in COMMAND_CENTER_THEME_CSS


def test_shell_injects_theme_identity_and_share_metadata_once():
    html = "<!doctype html><html><head><title>Workspace</title></head><body><main>Hi</main></body></html>"
    current = apply_visual_shell(html, _request("/workspace"))
    assert "data-esp-command-center-shell='1'" in current
    assert "href='/brand/theme.css'" in current
    assert "href='/favicon.webp'" in current
    assert "class='esp-command-center-shell'" in current
    assert "Elevate Souls Productions Content Creation Command Center" in current
    assert "https://command.example/brand/command-center-art.webp" in current
    again = apply_visual_shell(current, _request("/workspace"))
    assert again == current


def test_specific_legacy_presents_phrase_rewrites_cleanly():
    old = "Elevate Souls Productions Presents: The Live Sound Studio"
    current = rebrand_text(old)
    assert current == "Elevate Souls Productions Content Creation Command Center"
    assert "Presents:" not in current
    assert "Live Sound Studio" not in current


def test_ci_workflow_uses_current_visible_brand_name():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.startswith("name: Elevate Souls Command Center CI")
    assert "Install Command Center core + dev" in workflow
    assert "Pulsar-Frequency House CI" not in workflow
