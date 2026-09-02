from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _http_signature(route: Any) -> tuple[str, tuple[str, ...]] | None:
    """Return an exact HTTP route signature or None for mounts/websockets/helpers."""
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    normalized = tuple(sorted(str(method).upper() for method in methods))
    return path, normalized


def duplicate_http_signatures(routes: Iterable[Any]) -> dict[tuple[str, tuple[str, ...]], list[Any]]:
    grouped: dict[tuple[str, tuple[str, ...]], list[Any]] = {}
    for route in routes:
        signature = _http_signature(route)
        if signature is None:
            continue
        grouped.setdefault(signature, []).append(route)
    return {signature: items for signature, items in grouped.items() if len(items) > 1}


def deduplicate_http_routes(app: Any) -> list[dict[str, Any]]:
    """Remove unreachable exact duplicate HTTP routes while preserving first-match authority.

    Starlette/FastAPI dispatches routes in registration order, so when the same exact path and
    HTTP-method set is registered twice, every later copy is unreachable. Keeping those copies
    also causes duplicate OpenAPI operation IDs and makes route ownership ambiguous. This final
    composition pass preserves the first authoritative route exactly as runtime dispatch already
    does and removes only later routes with the same exact signature. Mounts, websocket routes,
    and different method sets are untouched.
    """

    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept: list[Any] = []
    removed: list[dict[str, Any]] = []

    for route in app.router.routes:
        signature = _http_signature(route)
        if signature is None or signature not in seen:
            kept.append(route)
            if signature is not None:
                seen.add(signature)
            continue

        endpoint = getattr(route, "endpoint", None)
        removed.append(
            {
                "path": signature[0],
                "methods": list(signature[1]),
                "name": getattr(route, "name", None),
                "endpoint": getattr(endpoint, "__qualname__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )

    if removed:
        app.router.routes[:] = kept

    app.state.route_integrity = {
        "duplicates_removed": len(removed),
        "removed": removed,
    }
    return removed


__all__ = ["deduplicate_http_routes", "duplicate_http_signatures"]
