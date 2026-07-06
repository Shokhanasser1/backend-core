"""Domain error hierarchy (interfaces doc §2.4; decision OV-07).

Typed exceptions instead of a Result wrapper: a single FastAPI handler maps
the hierarchy onto HTTP responses and i18n message keys (catalogs — Phase 3).
Webhook routes of payment providers are excluded from the generic handler and
answer in the provider dialect (Phase 3).
"""

from typing import ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "domain_error"
    message_key: ClassVar[str] = "errors.domain_error"
    http_status: ClassVar[int] = 400

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class NotFoundError(DomainError):
    code = "not_found"
    message_key = "errors.not_found"
    http_status = 404


class ConflictError(DomainError):
    code = "conflict"
    message_key = "errors.conflict"
    http_status = 409


class InvariantViolationError(DomainError):
    """Business-rule violation; not to be confused with Pydantic boundary validation."""

    code = "invariant_violation"
    message_key = "errors.invariant_violation"
    http_status = 422


class PermissionDeniedError(DomainError):
    code = "permission_denied"
    message_key = "errors.permission_denied"
    http_status = 403


class AuthenticationError(DomainError):
    code = "authentication_failed"
    message_key = "errors.authentication_failed"
    http_status = 401


class RateLimitedError(DomainError):
    code = "rate_limited"
    message_key = "errors.rate_limited"
    http_status = 429


class ExternalServiceError(DomainError):
    """Base for external port failures (payments, SMS, Telegram, SMTP)."""

    code = "external_service_error"
    message_key = "errors.external_service_error"
    http_status = 502


class PaymentProviderError(ExternalServiceError):
    code = "payment_provider_error"
    message_key = "errors.payment_provider_error"


class NotificationChannelError(ExternalServiceError):
    code = "notification_channel_error"
    message_key = "errors.notification_channel_error"


class WebhookVerificationError(DomainError):
    """Fallback only: webhook routes normally answer in the provider dialect."""

    code = "webhook_verification_failed"
    message_key = "errors.webhook_verification_failed"
    http_status = 403


class CircuitOpenError(ExternalServiceError):
    """Circuit breaker is open — fail fast instead of hammering a dead provider."""

    code = "circuit_open"
    message_key = "errors.circuit_open"
    http_status = 503
