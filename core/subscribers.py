"""Central registration of all core event subscribers.

Importing this module registers every core subscriber on the global bus. It
must be imported by BOTH the web process and the arq worker, because in-process
subscriptions only see events published in their own process and reliable
handlers must be resolvable in the worker (interfaces doc §2.6, topology).
"""

import core.audit.subscribers
import core.billing.receipts  # (payment/subscription receipts — Task 16)
import core.billing.subscribers  # noqa: F401  (auto-subscribe new tenants — OV-21)


def register_core_subscribers() -> None:
    """Idempotent no-op entry point — importing this module does the work.
    Exists so call sites read intentionally (`register_core_subscribers()`)."""
