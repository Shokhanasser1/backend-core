"""Pure-ASGI middleware: request_id, security headers, body-size cap, HTTP
metrics + access log."""

import re
import time
from collections.abc import Mapping
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import render_domain_error
from app.observability import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from shared.errors import PayloadTooLargeError
from shared.ids import new_uuid7

logger = structlog.stdlib.get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"
# An inbound id is echoed in a header and written to every log line: accept only
# a short token of safe characters (UUIDs, trace ids), otherwise mint our own.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _request_id(inbound: str | None) -> str:
    if inbound and _SAFE_REQUEST_ID.fullmatch(inbound):
        return inbound
    return new_uuid7().hex


SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("strict-transport-security", "max-age=31536000; includeSubDomains"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
)


class RequestIDMiddleware:
    """Accepts a well-formed inbound X-Request-ID or generates one; binds it to
    the logging context and echoes it in the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _request_id(headers.get(REQUEST_ID_HEADER))
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


class BodySizeLimitMiddleware:
    """Caps request bodies at the edge (413) before any handler buffers them:
    JSON is read whole and multipart uploads are spooled by Starlette, so a
    check inside the route comes too late. A declared Content-Length over the
    cap is refused unread; a chunked or understated body is cut off at the cap
    and whatever the app answered to the aborted read is replaced by the 413."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        self.app = app
        self._default_limit = default_limit
        self._path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._path_limits.get(scope["path"], self._default_limit)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send)
            return

        received = 0
        overflow = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, overflow
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    overflow = True
                    raise PayloadTooLargeError(f"request body over {limit} bytes")
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if overflow and not started:
                return  # the app's answer to the aborted read is replaced below
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not overflow or started:
                raise
        if overflow and not started:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        accept_language = Headers(scope=scope).get("accept-language")
        response = render_domain_error(PayloadTooLargeError(), accept_language)
        await response(scope, receive, send)


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
