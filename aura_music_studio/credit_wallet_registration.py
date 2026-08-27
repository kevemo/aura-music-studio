from __future__ import annotations

from .api import app
from .credit_wallet import router as credit_wallet_router


def install_credit_wallet_routes() -> None:
    """Attach credit-wallet routes once to the production FastAPI application."""

    existing = {getattr(route, "path", None) for route in app.router.routes}
    wanted = {getattr(route, "path", None) for route in credit_wallet_router.routes}
    if wanted - existing:
        app.include_router(credit_wallet_router)


install_credit_wallet_routes()
