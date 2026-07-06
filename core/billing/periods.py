"""Subscription period arithmetic (no external dependency)."""

import calendar
from datetime import datetime


def period_end(start: datetime, period: str) -> datetime:
    """End of a billing period. 'month' adds one calendar month (day clamped to
    month length); 'year' adds one year (Feb 29 clamped)."""
    if period == "year":
        year = start.year + 1
        day = min(start.day, calendar.monthrange(year, start.month)[1])
        return start.replace(year=year, day=day)
    # month
    month = start.month + 1
    year = start.year
    if month > 12:
        month = 1
        year += 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)
