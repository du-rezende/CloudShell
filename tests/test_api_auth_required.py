"""Generic test to ensure API endpoints don't succeed without authentication.

This test enumerates FastAPI's registered routes under `/api` and issues
unauthenticated requests. It skips known public endpoints like the health
check and the token creation endpoint. The assertion is that an unauthenticated
request must return a client error (4xx) and must not return a successful
2xx response or a server error (5xx).
"""
import re

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute
from unittest.mock import MagicMock, AsyncMock
from fastapi import WebSocket


def _example_path(path: str) -> str:
    """Replace path parameters like '{id}' with example values."""
    return re.sub(r"{[^}]+}", "1", path)


async def test_api_routes_require_auth(client):
    # Allowlist of public endpoints which must remain reachable without auth
    allowed_public = {
        ("POST", "/api/auth/token"),
        ("GET", "/api/health"),
    }

    from backend.main import app

    for route in app.routes:
        # HTTP routes (APIRoute)
        if isinstance(route, APIRoute):
            raw_path = getattr(route, "path", None)
            if raw_path is None:
                continue
            path = _example_path(raw_path)
            if not path.startswith("/api"):
                continue

            methods = [m for m in route.methods if m not in ("HEAD", "OPTIONS")]
            for method in methods:
                if (method, path) in allowed_public:
                    continue

                # Use a minimal payload for non-GET methods to trigger auth first
                if method in ("GET", "DELETE"):
                    resp = await client.request(method, path)
                else:
                    resp = await client.request(method, path, json={})

                # Unauthenticated requests must not succeed (2xx) and must not be server errors (5xx).
                assert 400 <= resp.status_code < 500, (
                    f"Unauthenticated {method} {path} returned {resp.status_code} (body={resp.text})"
                )

        # WebSocket routes (WebSocketRoute) — call handler directly with a mock WebSocket
        elif isinstance(route, WebSocketRoute):
            path = _example_path(route.path)
            if not path.startswith("/api"):
                continue

            # Build example path params to pass positionally to the endpoint
            raw_path = getattr(route, "path", "")


            # Create a mock WebSocket similar to other tests
            mock_ws = MagicMock(spec=WebSocket)
            mock_ws.query_params = {}
            mock_ws.headers = {}
            mock_ws.client = MagicMock()
            mock_ws.client.host = "127.0.0.1"
            mock_ws.accept = AsyncMock()
            mock_ws.close = AsyncMock()
            mock_ws.send_bytes = AsyncMock()

            # Call the endpoint: websocket handler usually has (/*path params*/, websocket)
            # Find how many path params are in the route (count of {..})
            param_count = raw_path.count("{")
            args = ["1"] * param_count + [mock_ws]

            # Invoke the WS handler and ensure it rejects unauthenticated connections
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue

            # Call the endpoint and accept any raised exceptions (unauthenticated path)
            try:
                await endpoint(*args)
            except Exception:
                pass

            # Handler should either have sent an error frame or closed the websocket
            assert mock_ws.send_bytes.called or mock_ws.close.called, (
                f"Unauthenticated WebSocket {path} did not send error or close"
            )
