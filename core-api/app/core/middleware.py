"""JWT authentication ASGI middleware.

Estrae il token Bearer dall'header Authorization, decodifica con
`decode_access_token`, e popola `request.state.token_payload`.

Implementato come **pure ASGI middleware** (non `BaseHTTPMiddleware`) per:
  - performance: niente overhead di copia request body / streaming wrap
  - controllo: rispondere 401 prima del handler senza creare un Response object

Comportamento:
  - Token assente            → request.state.token_payload = None, prosegue
  - Token presente e valido  → request.state.token_payload = <payload>, prosegue
  - Token presente e invalido → 401 immediata `{"detail": "<reason>"}`, NON prosegue

Path esclusi (passano senza ispezione header): /health, /docs, /docs/*, /redoc,
/openapi.json. Tutti gli altri path passano dal middleware.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from starlette.datastructures import State

from app.core.security import InvalidTokenError, decode_access_token

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

log = structlog.get_logger(__name__)

_EXCLUDED_EXACT = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def _is_excluded(path: str) -> bool:
    if path in _EXCLUDED_EXACT:
        return True
    # Sub-path Swagger (es. /docs/oauth2-redirect)
    return path.startswith("/docs/")


class JWTAuthMiddleware:
    """ASGI middleware: parses Bearer token, populates request.state.token_payload."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP scopes (lifespan, websocket): pass through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]

        if _is_excluded(path):
            await self.app(scope, receive, send)
            return

        # Pre-populate request.state.token_payload = None.
        # Nota: in Starlette recente scope["state"] può essere un dict (lifespan
        # state passthrough) invece di un oggetto State. Se è un dict, lo
        # wrappiamo in State (che usa quel dict come storage backing) per
        # ripristinare l'attribute access che `request.state.foo` si aspetta.
        existing = scope.get("state")
        if not isinstance(existing, State):
            backing = existing if isinstance(existing, dict) else None
            scope["state"] = State(backing)
        state = scope["state"]
        state.token_payload = None

        token = self._extract_bearer(scope)
        if token is None:
            await self.app(scope, receive, send)
            return

        try:
            payload = decode_access_token(token)
        except InvalidTokenError as exc:
            log.warning(
                "auth.token_invalid",
                path=path,
                method=scope.get("method"),
                reason=str(exc),
            )
            await self._send_401(send, detail=str(exc))
            return

        state.token_payload = payload
        log.debug(
            "auth.token_valid",
            user_id_from_token=payload.get("sub"),
            path=path,
            method=scope.get("method"),
        )
        await self.app(scope, receive, send)

    @staticmethod
    def _extract_bearer(scope: Scope) -> str | None:
        """Estrae il valore dopo 'Bearer ' dall'header Authorization (case-insensitive
        sul nome dell'header). Ritorna None se assente o malformato.
        """
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                # Header values are ASCII; latin-1 è una superset safe
                raw = value.decode("latin-1")
                if raw.startswith("Bearer "):
                    return raw.removeprefix("Bearer ").strip() or None
                return None  # Authorization presente ma non Bearer
        return None

    @staticmethod
    async def _send_401(send: Send, *, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer realm="api"'),
                ],
            },
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            },
        )
