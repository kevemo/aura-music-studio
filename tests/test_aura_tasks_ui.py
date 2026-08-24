from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from aura_music_studio.aura_tasks_ui import AuraTasksUIMiddleware, router


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

    app.add_middleware(AuraTasksUIMiddleware)
    return app


def test_tasks_ui_injects_only_into_aura_html():
    client = TestClient(_app())
    aura = client.get('/aura-intelligence')
    assert aura.status_code == 200
    assert '/aura-intelligence/tasks-ui.js' in aura.text
    assert '/aura-intelligence/tasks-ui.js' not in client.get('/other').text
    assert 'tasks-ui.js' not in client.get('/api-test').text


def test_tasks_ui_shows_worker_truthfulness_and_read_only_boundary():
    client = TestClient(_app())
    script = client.get('/aura-intelligence/tasks-ui.js')
    assert script.status_code == 200
    assert 'Task worker online' in script.text
    assert 'Task worker offline' in script.text
    assert 'Tasks can be saved now, but they will not execute until the worker process is running.' in script.text
    assert 'Background tasks are read-only.' in script.text
    assert 'interval_minutes' in script.text
    assert 'datetime-local' in script.text
