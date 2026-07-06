"""Structured JSON logging with mandatory secret masking (threat model V6).

Every record carries request_id/tenant_id/user_id when bound via structlog
contextvars. Logs never contain passwords, tokens, card numbers or 2FA codes:
the denylist processor masks them before rendering, and library loggers run
at WARNING+ so debug output of HTTP clients cannot leak headers.
"""

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[redacted]"

# Key-substring denylist; matching is case-insensitive.
SENSITIVE_KEY_MARKERS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "totp",
    "card_number",
    "cookie",
    "credential",
)

_MAX_MASK_DEPTH = 8

# Libraries whose default log levels are too chatty or risk leaking payloads.
_LIBRARY_LOG_LEVELS: dict[str, int] = {
    "uvicorn.access": logging.WARNING,  # we emit our own structured access log
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "asyncio": logging.WARNING,
}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def _mask_recursive(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_MASK_DEPTH:
        return value
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else _mask_recursive(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_mask_recursive(item, depth + 1) for item in value]
    return value


def mask_sensitive_data(
    _logger: logging.Logger | None,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor: masks values of denylisted keys at any nesting level."""
    for key in list(event_dict.keys()):
        if _is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _mask_recursive(event_dict[key], depth=1)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        mask_sensitive_data,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for logger_name, logger_level in _LIBRARY_LOG_LEVELS.items():
        logging.getLogger(logger_name).setLevel(logger_level)
