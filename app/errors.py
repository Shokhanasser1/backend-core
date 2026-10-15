"""The single DomainError -> HTTP rendering (OV-07), shared by the app's exception
handler and the edge middleware (which answers before any route runs)."""

from fastapi.responses import JSONResponse

from shared.error_catalog import ERROR_CATALOG
from shared.errors import DomainError
from shared.i18n import negotiate_locale, parse_accept_language


def render_domain_error(exc: DomainError, accept_language: str | None) -> JSONResponse:
    # message_key stays machine-readable; message is rendered in the request
    # locale (Accept-Language -> 'ru') via the i18n error catalog.
    locale = negotiate_locale(parse_accept_language(accept_language))
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message_key": exc.message_key,
                "message": ERROR_CATALOG.get(exc.message_key, locale),
                "detail": exc.detail,
            }
        },
    )
