from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_connectors_ui import AuraConnectorsUIMiddleware, router


def _app():
    app = FastAPI()
    app.include_router(router)

    @app.get('/aura-intelligence', response_class=HTMLResponse)
    def aura_page():
        return HTMLResponse('<html><body><div class="sideFoot"></div></body></html>')

    @app.get('/other', response_class=HTMLResponse)
    def other_page():
        return HTMLResponse('<html><body>Other</body></html>')

    @app.get('/api-test')
    def api_test():
        return JSONResponse({'ok': True})

    app.add_middleware(AuraConnectorsUIMiddleware)
    return app


def test_connectors_ui_injects_only_into_aura_html():
    client = TestClient(_app())
    assert '/aura-intelligence/connectors-ui.js' in client.get('/aura-intelligence').text
    assert '/aura-intelligence/connectors-ui.js' not in client.get('/other').text
    assert 'connectors-ui.js' not in client.get('/api-test').text


def test_connectors_ui_never_places_tokens_in_browser_contract():
    client = TestClient(_app())
    script = client.get('/aura-intelligence/connectors-ui.js')
    assert script.status_code == 200
    assert 'Connect Google' in script.text
    assert 'Drive search' in script.text
    assert 'Calendar events' in script.text
    assert 'Gmail search' in script.text
    assert 'encrypted server-side' in script.text
    assert 'access_token' not in script.text
    assert 'refresh_token' not in script.text
