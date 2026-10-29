from datetime import UTC, datetime

from app.web.formatting import fmt_input_datetime, fmt_money, fmt_number, fmt_time


def test_money():
    assert fmt_money(250, "fr", decimals=True) == "2,50 €"
    assert fmt_money(123450, "de", decimals=True) == "1.234,50 €"
    assert fmt_money(123450, "en", decimals=True) == "€1,234.50"
    assert fmt_money(1800, "fr") == "18 €"


def test_numbers_and_times_in_paris_time():
    assert fmt_number(7.2, "fr") == "7,2" and fmt_number(7.2, "en") == "7.2" and fmt_number(8.0, "de") == "8"
    winter = datetime(2026, 12, 1, 9, 30, tzinfo=UTC)
    assert fmt_time(winter) == "10:30"
    assert fmt_input_datetime(winter) == "2026-12-01T10:30"
