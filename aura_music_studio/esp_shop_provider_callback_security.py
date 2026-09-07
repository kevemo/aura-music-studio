from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import esp_shop_provider_runtime as runtime

router = APIRouter(tags=["ESP Shop Provider Callback Security"])
CALLBACK_PATH = "/command-center/shop-automation/oauth/{provider}/callback"


def _connection_for_unconsumed_state(store: runtime.ProviderRuntimeStore, provider: str, state: str) -> dict | None:
    provider = str(provider or "").strip().lower()
    digest = runtime._state_hash(str(state or ""))
    with store._connect() as con:
        row = con.execute(
            """SELECT connection_id,user_id FROM esp_shop_oauth_states
               WHERE state_sha256=? AND provider=? AND consumed_at IS NULL""",
            (digest, provider),
        ).fetchone()
    if not row:
        return None
    try:
        return store.base.connection(row["connection_id"], row["user_id"])
    except KeyError:
        return None


@router.get(CALLBACK_PATH, include_in_schema=False)
def secured_oauth_callback(provider: str, state: str, code: str, request: Request):
    if not runtime.RUNTIME_DB_PATH:
        raise HTTPException(503, "Shop provider runtime database is not configured")
    store = runtime.ProviderRuntimeStore(runtime.RUNTIME_DB_PATH)
    provider_key = str(provider or "").strip().lower()
    adapter = runtime.PROVIDER_ADAPTERS.get(provider_key)

    if adapter is not None and bool(getattr(adapter, "requires_signed_callback", False)):
        connection = _connection_for_unconsumed_state(store, provider_key, state)
        if connection is None:
            raise HTTPException(403, "OAuth state is invalid or already consumed")
        verifier = getattr(adapter, "verify_oauth_callback", None)
        if not callable(verifier):
            raise HTTPException(503, "Provider requires a signed OAuth callback verifier")
        callback_params = {str(key): str(value) for key, value in request.query_params.items()}
        try:
            verifier(connection, callback_params)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    try:
        result = store.complete_oauth(provider_key, state=state, code=code)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    connection_id = result["connection"]["id"]
    return RedirectResponse(
        f"/command-center/shop-automation?oauth=connected&connection={connection_id}",
        status_code=303,
    )


__all__ = ["router", "CALLBACK_PATH", "secured_oauth_callback"]
