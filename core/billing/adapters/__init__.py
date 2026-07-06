"""Payment provider adapters (interfaces §4.1).

UZ providers (Payme, Click) mostly call us (merchant callbacks), so an adapter
is: an outgoing checkout-URL builder + inbound-callback verification and
normalization + a provider-dialect response builder. Concrete adapters are NOT
public (ADR-0005): only PaymentService and the webhook routes touch them.

``build_payment_providers`` constructs the registry from configuration; the app
wires it into PaymentService and the webhook routes (task 17).
"""

from collections.abc import Mapping

from core.billing.adapters.click import ClickProvider
from core.billing.adapters.payme import PaymeProvider
from core.billing.ports import PaymentProvider
from shared.config import Settings
from shared.errors import InvariantViolationError

__all__ = ["ClickProvider", "PaymeProvider", "build_payment_providers"]


def build_payment_providers(settings: Settings) -> Mapping[str, PaymentProvider]:
    """Build the enabled providers from config. A provider listed in
    ``enabled_payment_providers`` but missing credentials is a misconfiguration —
    fail loudly at startup rather than at the first webhook."""
    providers: dict[str, PaymentProvider] = {}
    for code in settings.enabled_payment_provider_list:
        if code == PaymeProvider.code:
            providers[code] = PaymeProvider.from_settings(settings)
        elif code == ClickProvider.code:
            providers[code] = ClickProvider.from_settings(settings)
        else:
            raise InvariantViolationError(f"unknown payment provider in config: {code}")
    return providers
