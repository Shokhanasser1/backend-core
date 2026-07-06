"""Repositories for tenant-scoped billing tables."""

from core.billing.models import Payment, Subscription
from shared.repository import Repository


class PaymentRepository(Repository[Payment]):
    model = Payment


class SubscriptionRepository(Repository[Subscription]):
    model = Subscription
