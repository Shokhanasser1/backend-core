"""Request-scoped OrderService.

- ``order_service`` (storefront, buyer): built on the storefront bundle with
  ProductService + PaymentService (public interfaces of products / billing).
- ``staff_order_service`` (admin): built on the authed bundle; needs neither
  products nor payments (read-only staff view).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle, storefront_bundle
from core.billing.service import PaymentService
from modules.commerce.orders.service import OrderService
from modules.commerce.products import ProductService


async def order_service(
    request: Request, bundle: ServiceBundle = Depends(storefront_bundle)
) -> OrderService:
    state = request.app.state
    products = ProductService(bundle.uow, state.bus, bundle.ctx)
    payments = PaymentService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        providers=state.payment_providers,
        settings=state.settings,
    )
    return OrderService(bundle.uow, state.bus, bundle.ctx, products=products, payments=payments)


async def staff_order_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> OrderService:
    return OrderService(bundle.uow, request.app.state.bus, bundle.ctx)
