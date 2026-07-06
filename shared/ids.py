"""Application-side ID generation.

PK convention: UUIDv7 generated in the application (schema §1.3) — the ID is
known before INSERT, is time-ordered (B-tree locality) and not enumerable.
PostgreSQL 16 has no native uuidv7(), hence generation here.
"""

import uuid

import uuid_utils


def new_uuid7() -> uuid.UUID:
    """Return a UUIDv7 as a stdlib ``uuid.UUID`` instance."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
