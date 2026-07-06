from fastapi import Depends
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from shared.endpoint_markers import PUBLIC_ATTR
from shared.error_catalog import ERROR_CATALOG
from shared.errors import (
    AuthenticationError,
    CircuitOpenError,
    ConflictError,
    DomainError,
    ExternalServiceError,
    InvariantViolationError,
    NotFoundError,
    NotificationChannelError,
    PaymentProviderError,
    PermissionDeniedError,
    RateLimitedError,
    WebhookVerificationError,
)

EXPECTED_STATUSES = {
    NotFoundError: 404,
    ConflictError: 409,
    InvariantViolationError: 422,
    PermissionDeniedError: 403,
    AuthenticationError: 401,
    RateLimitedError: 429,
    ExternalServiceError: 502,
    PaymentProviderError: 502,
    NotificationChannelError: 502,
    WebhookVerificationError: 403,
    CircuitOpenError: 503,
}


def test_hierarchy_statuses_and_codes() -> None:
    for error_class, status in EXPECTED_STATUSES.items():
        assert issubclass(error_class, DomainError)
        assert error_class.http_status == status
        assert error_class.code != DomainError.code
        assert error_class.message_key.startswith("errors.")


def test_external_errors_share_base() -> None:
    assert issubclass(PaymentProviderError, ExternalServiceError)
    assert issubclass(NotificationChannelError, ExternalServiceError)
    assert issubclass(CircuitOpenError, ExternalServiceError)


def test_domain_error_is_mapped_to_http_response() -> None:
    application = create_app(Settings(_env_file=None))

    async def marker() -> None:
        return None

    setattr(marker, PUBLIC_ATTR, "test route")

    @application.get("/boom", dependencies=[Depends(marker)])
    async def boom() -> None:
        raise NotFoundError("gadget 42 not found")

    # No context manager = no lifespan, so this test needs no external services.
    client = TestClient(application)
    response = client.get("/boom")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message_key"] == "errors.not_found"
    assert body["error"]["detail"] == "gadget 42 not found"
    # message is localized via the i18n error catalog (default locale = ru).
    assert body["error"]["message"] == ERROR_CATALOG.get("errors.not_found", "ru")

    # Accept-Language negotiates the localized message (uz).
    uz = client.get("/boom", headers={"Accept-Language": "uz-UZ,uz;q=0.9"})
    assert uz.json()["error"]["message"] == ERROR_CATALOG.get("errors.not_found", "uz")
