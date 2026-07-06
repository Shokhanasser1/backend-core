"""Password hashing with argon2id (engineering standards; threat model V2).

argon2id makes an offline dump attack expensive in memory and time. Verifying a
non-existent user against a dummy hash keeps login timing uniform (no user
enumeration, V2).
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

# A precomputed hash to verify against when the user does not exist, so the
# response time matches a real verification (timing uniformity).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-uniformity")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify a password; when password_hash is None (unknown user) still spend
    the time verifying a dummy hash, then return False."""
    target = password_hash or _DUMMY_HASH
    try:
        _hasher.verify(target, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Malformed hash etc. — treat as failure, never leak details.
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
