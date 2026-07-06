"""core/billing — plans, subscriptions, payments; adapters Payme/Click.

Universal payment intake used by billing itself and (Phase 6) by commerce
through the public PaymentService. Adapters and the payment state machine are
internals; the public interface is re-exported here as it is built.
"""
