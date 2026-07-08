"""Public payment webhook routes (interfaces §4.1, threat model V4).

These endpoints are authenticated by the provider signature, NOT by JWT, so they
carry the ``public_endpoint`` marker. Every outcome is answered in the provider's
own dialect (JSON-RPC for Payme, error codes for Click) with HTTP 200 — they are
deliberately kept out of the generic DomainError->HTTP handler: a blanket 4xx
would break provider retries and reconciliation. Only an unrecognizable request
(no dialect to answer in) yields the WebhookVerificationError 403 fallback.

All processing lives in ``WebhookProcessor``; the route only reconstructs the raw
request and renders the dialect response. The processor is built per request from
``app.state`` (wired in the lifespan) so core never imports app.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from core.auth.deps import public_endpoint
from core.billing.ports import RawWebhook
from core.billing.webhooks import WebhookProcessor
from shared.errors import WebhookVerificationError

router = APIRouter(prefix="/api/billing/webhooks", tags=["billing-webhooks"])

_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def _processor(request: Request) -> WebhookProcessor:
    state = request.app.state
    return WebhookProcessor(
        maintenance_sessions=state.db.maintenance_sessions,
        bus=state.bus,
        providers=state.payment_providers,
        settings=state.settings,
    )


async def _raw_webhook(request: Request) -> RawWebhook:
    body = (await request.body()).decode("utf-8", errors="replace")
    form: dict[str, str] = {}
    content_type = request.headers.get("content-type", "")
    if any(ct in content_type for ct in _FORM_CONTENT_TYPES):
        # Starlette caches the body, so form() reparses the same bytes read above.
        form = {key: str(value) for key, value in (await request.form()).items()}
    return RawWebhook(headers=dict(request.headers), body=body, form=form)


async def _handle(request: Request, provider_code: str) -> JSONResponse:
    raw = await _raw_webhook(request)
    try:
        status_code, body = await _processor(request).process(provider_code, raw)
    except WebhookVerificationError:
        # No provider dialect could be determined -> the only non-dialect answer.
        return JSONResponse(
            status_code=403, content={"error": {"code": "webhook_verification_failed"}}
        )
    return JSONResponse(status_code=status_code, content=body)


@router.post(
    "/payme",
    dependencies=[Depends(public_endpoint(reason="Payme merchant signature auth (V4)"))],
)
async def payme_webhook(request: Request) -> JSONResponse:
    return await _handle(request, "payme")


@router.post(
    "/click",
    dependencies=[Depends(public_endpoint(reason="Click merchant signature auth (V4)"))],
)
async def click_webhook(request: Request) -> JSONResponse:
    return await _handle(request, "click")


@router.post(
    "/stripe",
    dependencies=[Depends(public_endpoint(reason="Stripe webhook signature auth (V4)"))],
)
async def stripe_webhook(request: Request) -> JSONResponse:
    return await _handle(request, "stripe")
