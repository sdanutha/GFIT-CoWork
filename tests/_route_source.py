"""Test support: the source of the handler the route table dispatches a request to.

Source-text tests used to slice ``api/routes.py`` around a route's
``parsed.path == "..."`` check. Routes are dispatched through the route table
now; these find the handler a request reaches and return its source.
"""
from __future__ import annotations

import inspect

from api.route_table import ROUTES

# The route table's session routes, as (method, pattern) -> READ or WRITE.
SESSION_ROUTE_KINDS = {(route.method, route.pattern): route.session for route in ROUTES if route.session is not None}


def route_handler(method: str, path: str):
    """The route module's function that serves *method* *path* (AssertionError if none)."""
    import api.routes as routes
    from api import route_table

    route = route_table.match(method, path)
    assert route is not None and route.handler, f"no route serves {method} {path}"
    return getattr(routes, route.handler)


def route_source(method: str, path: str) -> str:
    """The source of the function that serves *method* *path*."""
    return inspect.getsource(route_handler(method, path))
