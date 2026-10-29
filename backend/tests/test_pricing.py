from datetime import datetime, timedelta

import pytest

from app.services.pricing import price_for_stay

T0 = datetime(2026, 10, 28, 8, 0)


def stay(minutes, **kw):
    return price_for_stay(T0, T0 + timedelta(minutes=minutes), hourly_cents=250, daily_cents=1800, **kw)


def test_started_hour():
    assert stay(1) == 250
    assert stay(60) == 250
    assert stay(61) == 500
    assert stay(180) == 750


def test_free_minutes():
    assert stay(10, free_minutes=15) == 0
    assert stay(16, free_minutes=15) == 250


def test_daily_cap_and_multi_day():
    assert stay(10 * 60) == 1800          # 10 h would be 25 € -> capped at the daily pass
    assert stay(24 * 60) == 1800
    assert stay(24 * 60 + 30) == 1800 + 250


def test_other_modes():
    assert stay(20, billing_mode="quarter_hour") == 2 * 63
    assert stay(30, billing_mode="minute") == 125


def test_unknown_mode():
    with pytest.raises(ValueError):
        stay(30, billing_mode="weekly")
