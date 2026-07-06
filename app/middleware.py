"""Pure-ASGI middleware: request_id, security headers, HTTP metrics + access log."""

import time
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from shared.ids import new_uuid7

logger = structlog.stdlib.get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("strict-transport-security", "max-age=31536000; includeSubDomains"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
)


class RequestIDMiddleware:
    """Accepts an inbound X-Request-ID or generates one; binds it to the
    logging context and echoes it in the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER) or new_uuid7().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")


class SecurityHeadersMiddleware:
    """HSTS, nosniff, frame denial and strict referrer policy on every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    response_headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class MetricsMiddleware:
    """Latency histogram + status counter per route template, and a structured
    access-log line. Route template (not raw path) keeps label cardinality low."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        started_at = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def send_capturing_status(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capturing_status)
        finally:
            duration = time.perf_counter() - started_at
            route: Any = scope.get("route")
            path_template = getattr(route, "path", None) or "<unmatched>"
            status = status_holder.get("status", 500)
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path_template, status=str(status)).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path_template).observe(
                duration
            )
            logger.info(
                "http_request",
                method=method,
                path=path_template,
                status=status,
                duration_ms=round(duration * 1000, 2),
            )
