"""Payment webhook processing (interfaces §4.1/§4.2, threat model V4).

Security-critical intake for Payme/Click merchant callbacks. Invariants:

- **Authenticity by provider signature, not by source** — the adapter verifies
  the Payme Basic-auth / Click ``sign_string`` (constant time) and reports
  ``signature_valid``. An unrecognizable request (no dialect to answer in) is the
  only ``WebhookVerificationError`` -> HTTP 403 fallback; every other outcome is
  answered in the provider's own dialect.
- **Tenant binding derived ONLY from our records** — after authentication the
  payment is located via ``SystemRepository`` (system context, read-only) by our
  ``payment_id`` or ``(provider, provider_transaction_id)``; the context is then
  ELEVATED (§2.1) to ``payment.tenant_id`` and the state transition runs under it.
  A webhook for an unknown payment is rejected as an anomaly. Consequence: a
  callback for tenant A can never touch tenant B.
- **Effectively-once** — the ``payment_webhooks (provider, dedup_key)`` unique
  ledger row and the payment transition commit in ONE app_maintenance
  transaction. A concurrent duplicate blocks on the ledger INSERT until the first
  commits, then replays the answer deterministically from payment state (§2.6).
- **No poisoning** — the ledger dedup key is only ever written by an
  authenticated callback. Rejected (bad-signature) traffic is journalled under a
  separate ``rejected:<hash>`` namespace so it can never occupy the legitimate
  ``(provider_txn_id, action)`` key of a real callback.

The payment state machine lives in ``PaymentService`` (not here); this module
routes an authenticated, tenant-bound callback to the right ``mark_*`` and maps
the result to a ``CallbackOutcome`` the adapter renders.
"""

import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.billing.models import Payment, PaymentWebhook
from core.billing.ports import (
    CallbackOutcome,
    OutcomeStatus,
    PaymentProvider,
    ProviderCallback,
    RawWebhook,
)
from core.billing.service import PaymentService, _payment_dto
from shared.config import Settings
from shared.context import Actor, TenantContext
from shared.db import apply_tenant_context
from shared.errors import InvariantViolationError, WebhookVerificationError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.money import Money
from shared.service import SqlAlchemyUnitOfWork
from shared.system_repository import SystemRepository

logger = logging.getLogger(__name__)

# Read-only callbacks: no state change, no dedup ledger (idempotent by nature).
_READ_ONLY_ACTIONS = frozenset({"check", "status"})
# State-changing callbacks reach a mark_* transition.
_STATE_CHANGING_ACTIONS = frozenset({"create", "confirm", "cancel"})

# Header values redacted before journalling (schema §2.6): channel secrets never
# reach the DB. Authorization keeps only its scheme name.
_REDACT_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "x-api-key"})


class _PaymentSystemRepo(SystemRepository[Payment]):
    """Tenant-filter-free lookup of a payment to identify it before the tenant
    context exists (the single legitimate SystemRepository use — §2.1)."""

    model = Payment


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered == "authorization":
            scheme = str(value).split(" ", 1)[0] if value else ""
            out[str(key)] = f"{scheme} [redacted]".strip()
        elif lowered in _REDACT_HEADERS:
            out[str(key)] = "[redacted]"
        else:
            out[str(key)] = str(value)
    return out


def _now() -> datetime:
    return datetime.now(UTC)


class WebhookProcessor:
    """Processes one merchant callback end-to-end in a single app_maintenance
    transaction. Constructed per request from ``app.state`` (the router)."""

    def __init__(
        self,
        *,
        maintenance_sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        providers: dict[str, PaymentProvider],
        settings: Settings,
    ) -> None:
        self._sessions = maintenance_sessions
        self._bus = bus
        self._providers = providers
        self._settings = settings

    def _system_ctx(self, provider_code: str) -> TenantContext:
        return TenantContext(
            tenant_id=None, actor=Actor(kind="integration", id=provider_code), request_id=None
        )

    async def process(self, provider_code: str, raw: RawWebhook) -> tuple[int, dict[str, object]]:
        """Verify, route and answer a callback. Returns (http_status, body) in the
        provider dialect. Raises WebhookVerificationError only for an
        unrecognizable request (the router maps it to 403)."""
        provider = self._providers.get(provider_code)
        if provider is None:
            raise WebhookVerificationError(f"unknown or disabled provider: {provider_code}")
        # parse_webhook is sync (follows the adapters); raises for unrecognizable input.
        callback = provider.parse_webhook(raw)

        async with SqlAlchemyUnitOfWork(
            self._sessions, context=self._system_ctx(provider_code)
        ) as uow:
            outcome = await self._dispatch(uow, provider, provider_code, callback, raw)
        response = provider.build_webhook_response(outcome)
        return response.status_code, dict(response.body)

    async def _dispatch(
        self,
        uow: SqlAlchemyUnitOfWork,
        provider: PaymentProvider,
        provider_code: str,
        callback: ProviderCallback,
        raw: RawWebhook,
    ) -> CallbackOutcome:
        if callback.action in _READ_ONLY_ACTIONS:
            return await self._handle_read_only(uow.session, callback)

        if not callback.signature_valid:
            # Answer invalid_signature in the dialect; journal WITHOUT touching the
            # legitimate dedup namespace (anti-poisoning).
            await self._journal_rejected(uow.session, provider_code, raw)
            logger.warning(
                "payment webhook rejected: invalid signature",
                extra={"provider": provider_code, "action": callback.action},
            )
            return CallbackOutcome(status="invalid_signature", callback=callback)

        dedup_key = f"{callback.provider_txn_id}:{callback.action}"
        webhook = await self._claim_ledger(uow.session, provider_code, dedup_key, raw)
        if webhook is None:
            # Concurrent/duplicate delivery: the first row is committed — replay.
            return await self._replay(uow.session, provider_code, dedup_key, callback)
        return await self._process_transition(uow, provider_code, callback, webhook)

    # --- read-only (check / status): no ledger, no state change ---

    async def _handle_read_only(
        self, session: AsyncSession, callback: ProviderCallback
    ) -> CallbackOutcome:
        if not callback.signature_valid:
            return CallbackOutcome(status="invalid_signature", callback=callback)
        payment = await self._find_payment(session, callback)
        if payment is None:
            return CallbackOutcome(status="not_found", callback=callback)
        dto = _payment_dto(payment)
        if not self._amount_ok(callback, payment):
            return CallbackOutcome(status="amount_mismatch", payment=dto, callback=callback)
        if callback.action == "check" and payment.status not in ("created", "pending"):
            return CallbackOutcome(status="invalid_state", payment=dto, callback=callback)
        return CallbackOutcome(status="ok", payment=dto, callback=callback)

    # --- state-changing (create / confirm / cancel) ---

    async def _claim_ledger(
        self, session: AsyncSession, provider_code: str, dedup_key: str, raw: RawWebhook
    ) -> PaymentWebhook | None:
        """Insert the ledger row for this (provider, dedup_key). Returns the row
        on success, or None if a row already exists (duplicate). ON CONFLICT DO
        NOTHING blocks until a concurrent inserter commits/rolls back, which is
        what serializes concurrent deliveries (schema §2.6)."""
        stmt = (
            pg_insert(PaymentWebhook)
            .values(
                id=new_uuid7(),
                provider=provider_code,
                dedup_key=dedup_key,
                raw_body=raw.body,
                headers=_redact_headers(raw.headers),
                signature_valid=True,
                status="received",
            )
            .on_conflict_do_nothing(index_elements=["provider", "dedup_key"])
            .returning(PaymentWebhook.id)
        )
        inserted_id = (await session.execute(stmt)).scalar_one_or_none()
        if inserted_id is None:
            return None
        return await session.get(PaymentWebhook, inserted_id)

    async def _process_transition(
        self,
        uow: SqlAlchemyUnitOfWork,
        provider_code: str,
        callback: ProviderCallback,
        webhook: PaymentWebhook,
    ) -> CallbackOutcome:
        session = uow.session
        payment = await self._find_payment(session, callback)
        if payment is None:
            webhook.status = "rejected"
            webhook.error = "payment not found"
            webhook.processed_at = _now()
            logger.warning(
                "payment webhook for unknown payment",
                extra={"provider": provider_code, "provider_txn_id": callback.provider_txn_id},
            )
            return CallbackOutcome(status="not_found", callback=callback)

        # Context elevation (§2.1): from here the work runs in the payment's tenant.
        tenant_ctx = TenantContext(
            tenant_id=payment.tenant_id,
            actor=Actor(kind="integration", id=provider_code),
            request_id=None,
        )
        await apply_tenant_context(session, tenant_ctx)
        webhook.tenant_id = payment.tenant_id
        webhook.payment_id = payment.id

        if not self._amount_ok(callback, payment):
            webhook.status = "rejected"
            webhook.error = "amount mismatch"
            webhook.processed_at = _now()
            logger.warning(
                "payment webhook amount mismatch",
                extra={"payment_id": str(payment.id), "provider": provider_code},
            )
            return CallbackOutcome(
                status="amount_mismatch", payment=_payment_dto(payment), callback=callback
            )

        payments = PaymentService(
            uow, self._bus, tenant_ctx, providers=self._providers, settings=self._settings
        )
        try:
            await self._apply_action(payments, payment, callback)
        except InvariantViolationError as exc:
            webhook.status = "failed"
            webhook.error = str(exc)
            webhook.processed_at = _now()
            return CallbackOutcome(
                status="invalid_state", payment=_payment_dto(payment), callback=callback
            )

        webhook.status = "processed"
        webhook.processed_at = _now()
        return CallbackOutcome(status="ok", payment=_payment_dto(payment), callback=callback)

    async def _apply_action(
        self, payments: PaymentService, payment: Payment, callback: ProviderCallback
    ) -> None:
        if callback.action == "create":
            # created -> pending; a create for an already-open payment is invalid.
            if payment.status != "created":
                raise InvariantViolationError(f"create for payment in state {payment.status}")
            await payments.mark_pending(payment, provider_txn_id=callback.provider_txn_id or "")
        elif callback.action == "confirm":
            await payments.mark_succeeded(payment)
        elif callback.action == "cancel":
            await payments.mark_canceled_by_provider(payment)
        else:  # pragma: no cover - guarded by _STATE_CHANGING_ACTIONS
            raise InvariantViolationError(f"unsupported action: {callback.action}")

    async def _replay(
        self, session: AsyncSession, provider_code: str, dedup_key: str, callback: ProviderCallback
    ) -> CallbackOutcome:
        """Answer a duplicate delivery deterministically from the committed state
        (schema §2.3): the response body is not stored, it is recomputed."""
        webhook = (
            await session.execute(
                select(PaymentWebhook)
                .where(
                    PaymentWebhook.provider == provider_code,
                    PaymentWebhook.dedup_key == dedup_key,
                )
                .with_for_update()
            )
        ).scalar_one()
        payment = (
            await session.get(Payment, webhook.payment_id)
            if webhook.payment_id is not None
            else None
        )
        dto = _payment_dto(payment) if payment is not None else None
        if webhook.status == "rejected":
            rejected: OutcomeStatus = (
                "amount_mismatch" if (webhook.error or "").startswith("amount") else "not_found"
            )
            return CallbackOutcome(status=rejected, payment=dto, callback=callback)
        if webhook.status == "failed":
            return CallbackOutcome(status="invalid_state", payment=dto, callback=callback)
        if payment is None:
            return CallbackOutcome(status="not_found", callback=callback)
        return CallbackOutcome(status="already_processed", payment=dto, callback=callback)

    # --- helpers ---

    async def _find_payment(
        self, session: AsyncSession, callback: ProviderCallback
    ) -> Payment | None:
        """Locate the payment from OUR records only (V4). Prefer our payment_id
        (the merchant order id, carried by create/Click callbacks); fall back to
        the stored (provider, provider_transaction_id) for confirm/cancel/status."""
        repo = _PaymentSystemRepo(session)
        reference = callback.payment_reference
        if reference:
            payment_id = _as_uuid(reference)
            if payment_id is not None:
                found = await repo.get_one_or_none(
                    Payment.id == payment_id, Payment.provider == callback.provider
                )
                if found is not None:
                    return found
        if callback.provider_txn_id:
            return await repo.get_one_or_none(
                Payment.provider == callback.provider,
                Payment.provider_transaction_id == callback.provider_txn_id,
            )
        return None

    def _amount_ok(self, callback: ProviderCallback, payment: Payment) -> bool:
        """Reconcile amount/currency before any transition (V4). A zero callback
        amount means the provider did not carry one for this action (confirm/
        cancel/status) — nothing to reconcile."""
        if callback.amount.amount <= 0:
            return True
        return callback.amount == Money(amount=payment.amount, currency=payment.currency)

    async def _journal_rejected(
        self, session: AsyncSession, provider_code: str, raw: RawWebhook
    ) -> None:
        """Record bad-signature traffic for forensics under a non-colliding key so
        an unauthenticated request cannot poison a legitimate dedup key."""
        digest = hashlib.sha256(f"{raw.body}|{sorted(raw.form.items())}".encode()).hexdigest()
        stmt = (
            pg_insert(PaymentWebhook)
            .values(
                id=new_uuid7(),
                provider=provider_code,
                dedup_key=f"rejected:{digest}",
                raw_body=raw.body,
                headers=_redact_headers(raw.headers),
                signature_valid=False,
                status="rejected",
                error="invalid signature",
                processed_at=_now(),
            )
            .on_conflict_do_nothing(index_elements=["provider", "dedup_key"])
        )
        await session.execute(stmt)


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


__all__ = ["WebhookProcessor"]
