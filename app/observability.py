"""Sentry (with mandatory PII scrubbing) and Prometheus metrics.

Sentry policy (threat model §3.4, V6): pseudonyms leave the perimeter, not
persons — send_default_pii=False, no local variables in stack traces, request
bodies/headers/cookies stripped, e-mails and phone numbers masked in messages.
Self-hosted Sentry is a deployment option: the DSN comes from the environment,
the code does not care (OV-28).
"""

import re
from typing import Any, cast

import sentry_sdk
from prometheus_client import Counter, Gauge, Histogram
from sentry_sdk.types import Event, Hint

from app.config import Settings
from app.logging_setup import REDACTED, mask_sensitive_data
from shared import TEMPLATE_VERSION

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# E.164-like sequences of 9+ digits, optional leading + and separators.
_PHONE_RE = re.compile(r"\+?\d[\d ()-]{7,}\d")


def _mask_pii_text(text: str) -> str:
    text = _EMAIL_RE.sub(REDACTED, text)
    return _PHONE_RE.sub(REDACTED, text)


def scrub_sentry_event(event: Event, _hint: Hint | None = None) -> Event | None:
    """before_send hook: best-effort scrubbing on top of the hard guarantees
    (send_default_pii=False, include_local_variables=False)."""
    data = cast("dict[str, Any]", event)
    request = data.get("request")
    if isinstance(request, dict):
        # Bodies, headers and cookies are never attached; URL/method stay.
        for field in ("data", "headers", "cookies", "env", "query_string"):
            request.pop(field, None)

    message = data.get("message")
    if isinstance(message, str):
        data["message"] = _mask_pii_text(message)
    logentry = data.get("logentry")
    if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
        logentry["message"] = _mask_pii_text(logentry["message"])

    extra = data.get("extra")
    if isinstance(extra, dict):
        data["extra"] = dict(mask_sensitive_data(None, "", extra))
    return event


def init_sentry(settings: Settings) -> bool:
    """DSN from the environment; empty DSN = Sentry off. Returns True if enabled."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=f"backend-core@{TEMPLATE_VERSION}",
        send_default_pii=False,
        include_local_variables=False,
        before_send=scrub_sentry_event,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True


# --- Prometheus metrics (default registry, scraped via GET /metrics) ---

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path"),
)
ARQ_QUEUE_DEPTH = Gauge(
    "arq_queue_depth",
    "Number of jobs waiting in the arq queue",
    labelnames=("queue",),
)
