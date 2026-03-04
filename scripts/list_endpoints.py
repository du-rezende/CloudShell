#!/usr/bin/env python3
"""
Enumerate FastAPI endpoints (HTTP and WebSocket) and print a Markdown table
showing path, methods, type and whether they appear to be authenticated.

This script uses two heuristics:
- HTTP routes (APIRoute): mark as authenticated if one of the route's
  dependencies resolves to `get_current_user`.
- WebSocket routes: inspect the endpoint source for obvious token/JWT checks
  (e.g. presence of 'token', 'jose_jwt', or 'websocket.query_params').

The output is printed as a Markdown table so it appears nicely in GitHub Actions logs.
"""
from __future__ import annotations

import inspect
import sys
from typing import List, Tuple

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

try:
    # Import the FastAPI app
    from backend import main as backend_main
    from backend.routers.auth import get_current_user
except Exception as exc:  # pragma: no cover - runtime safety for CI
    print("Failed to import application or auth helper:", exc, file=sys.stderr)
    raise


def classify_http_route(route: APIRoute) -> bool:
    """Return True if route appears to require authentication."""
    try:
        deps = getattr(route, "dependant", None)
        if deps and hasattr(deps, "dependencies"):
            for d in deps.dependencies:
                call = getattr(d, "call", None)
                if call is get_current_user:
                    return True
                # Fallback: compare by name (catches get_current_user, _get_payload, etc.)
                call_name = getattr(call, "__name__", None)
                if call_name in ("get_current_user", "_get_payload"):
                    return True
    except Exception:
        # Be permissive on failure: assume public
        return False
    return False


def classify_ws_route(route: WebSocketRoute) -> bool:
    """Return True if websocket endpoint source hints at authentication."""
    try:
        endpoint = route.endpoint
        src = inspect.getsource(endpoint)
        hints = ("token", "jose_jwt", "websocket.query_params", "get_current_user")
        for h in hints:
            if h in src:
                return True
    except Exception:
        return False
    return False


def gather() -> List[Tuple[str, str, str, str]]:
    app = backend_main.app
    out = []
    for route in app.routes:
        # APIRoute covers usual HTTP endpoints
        if isinstance(route, APIRoute):
            path = route.path
            methods = ",".join(sorted(route.methods)) if getattr(route, "methods", None) else ""
            auth = classify_http_route(route)
            out.append((path, methods, "HTTP", "authenticated" if auth else "public"))
        # WebSocketRoute covers WS endpoints
        elif isinstance(route, WebSocketRoute):
            path = route.path
            auth = classify_ws_route(route)
            out.append((path, "-", "WebSocket", "authenticated" if auth else "public"))
    return out


def print_md_table(rows: List[Tuple[str, str, str, str]]) -> None:
    print("| Path | Methods | Type | Access |")
    print("| --- | --- | --- | --- |")
    for path, methods, rtype, access in rows:
        print(f"| {path} | {methods or '-'} | {rtype} | {access} |")


def main() -> int:
    rows = gather()
    # Sort by path for stable output
    rows.sort(key=lambda r: (r[2], r[0]))
    print_md_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
