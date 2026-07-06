"""Event-bus deduplication table (schema doc §2.7).

Global service table — a sanctioned exception from tenant RLS: contains no
business data, event_id is globally unique, and platform events (tenant_id
is None in the envelope) must deduplicate the same way. Rows are written and
read only by the bus dispatcher; retention sweep arrives in Phase 4.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import GlobalBase


class ProcessedEvent(GlobalBase):
    __tablename__ = "processed_events"

    # Composite PK (handler, event_id) IS the deduplication mechanism.
    handler: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # Immutable rows: no created_at/updated_at convention here (schema §1.3 exception).
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_processed_events_processed_at", "processed_at"),)
