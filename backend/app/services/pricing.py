"""Price calculation for a stay, from the current tariffs and the billing settings."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def price_for_stay(
    start: datetime,
    end: datetime,
    hourly_cents: int,
    daily_cents: int,
    billing_mode: str = "started_hour",
    free_minutes: int = 0,
) -> int:
    """Return the price in cents. The daily pass caps each started 24-hour block."""
    minutes = max(0, math.ceil((end - start) / timedelta(minutes=1)))
    if minutes <= free_minutes:
        return 0
    full_days, rest = divmod(minutes, 24 * 60)
    if billing_mode == "started_hour":
        rest_price = math.ceil(rest / 60) * hourly_cents
    elif billing_mode == "quarter_hour":
        rest_price = math.ceil(rest / 15) * math.ceil(hourly_cents / 4)
    elif billing_mode == "minute":
        rest_price = math.ceil(rest * hourly_cents / 60)
    else:
        raise ValueError(f"unknown billing mode {billing_mode}")
    return full_days * daily_cents + min(rest_price, daily_cents)
