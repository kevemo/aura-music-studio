from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_ui_extension import AuraUIExtensionMiddleware, router


def _app():
    app = FastAPI()
    app.include_router(router)

    @app.get("/aura-intelligence", response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse("<html><body><main>Aura</main></body></html>")

    @app.get("/other", response_class=HTMLResponse)
    def other_page():
        return HTMLResponse("<html><body>Other</body></html>")

    @app.get("/api-test")
    def api_test():
        return JSONResponse({"ok": True})

    app.add_middleware(AuraUIExtensionMiddleware)
    return app


def test_extension_injects_only_into_aura_html():
    client = TestClient(_app())
    aura = client.get("/aura-intelligence")
    assert aura.status_code == 200
    assert "/aura-intelligence/ui-extension.js" in aura.text

    other = client.get("/other")
    assert other.status_code == 200
    assert "/aura-intelligence/ui-extension.js" not in other.text

    api = client.get("/api-test")
    assert api.json() == {"ok": True}
    assert "ui-extension.js" not in api.text


def test_extension_script_exposes_reasoning_and_project_controls():
    client = TestClient(_app())
    script = client.get("/aura-intelligence/ui-extension.js")
    assert script.status_code == 200
    assert "reasoning-mode" in script.text
    assert "attachments/${encodeURIComponent(button.dataset.promoteAttachment)}/promote" in script.text
    assert "rights_confirmed:true" in script.text
