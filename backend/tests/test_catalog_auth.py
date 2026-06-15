"""Catalog read endpoints must require authentication.

Several GET endpoints in the offerings / presets / images catalog routers were reachable without a
token (no per-route ``get_current_principal`` dependency and no global auth middleware). These are
authenticated-but-open reads available to ANY valid principal — they must still require a token.

There is no HTTP harness in this suite (the domain tests drive services directly), so we assert the
contract at the route level: each catalog-read handler must declare
``Depends(get_current_principal)`` so FastAPI rejects anonymous callers with 401 before the handler
runs. """
from __future__ import annotations

import inspect

from app.api import images_router, offerings_router, presets_router
from app.api.deps import get_current_principal


def _route_by_path_method(router, path: str, method: str):
    for r in router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _depends_on_principal(route) -> bool:
    """True iff the route's resolved dependency tree includes get_current_principal."""
    def _walk(dependant) -> bool:
        if getattr(dependant, "call", None) is get_current_principal:
            return True
        return any(_walk(sub) for sub in getattr(dependant, "dependencies", []))

    return any(_walk(sub) for sub in route.dependant.dependencies)


CATALOG_READS = [
    (offerings_router.router, "/offerings", "GET"),
    (offerings_router.router, "/offerings/{offering_id}", "GET"),
    (offerings_router.router, "/offerings/{offering_id}/price-history", "GET"),
    (presets_router.router, "/resource-presets", "GET"),
    (presets_router.router, "/resource-presets/{preset_id}", "GET"),
    (images_router.router, "/images", "GET"),
    (images_router.router, "/images/{image_id}", "GET"),
]


def test_catalog_reads_require_authentication():
    for router, path, method in CATALOG_READS:
        route = _route_by_path_method(router, path, method)
        assert _depends_on_principal(route), (
            f"{method} {path} is missing Depends(get_current_principal) — "
            "it would be reachable without a token"
        )


def test_price_history_handler_does_not_leak_changed_by():
    """The price-history response must not surface changed_by (leaks admin user IDs)."""
    src = inspect.getsource(offerings_router.price_history)
    assert "changed_by" not in src, "price-history response must not include changed_by"
