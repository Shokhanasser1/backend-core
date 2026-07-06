"""core/auth — registration, JWT (access+refresh), 2FA (TOTP), RBAC.

Public interface is re-exported here as it is built (services, DTOs, deps).
Internals (models, token signing, hashing, rate limiter) stay private.
"""
