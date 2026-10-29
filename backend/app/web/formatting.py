"""Locale-aware formatting used by the templates (dates in the site time zone, money)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")

MONTHS = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."],
}
WEEKDAYS = {
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
}


def local(value: datetime | None) -> datetime | None:
    return value.astimezone(TZ) if value else None


def fmt_time(value: datetime | None) -> str:
    return local(value).strftime("%H:%M") if value else "—"


def fmt_date(value: date | datetime | None, locale: str) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = local(value)
    month = MONTHS[locale][value.month - 1]
    if locale == "en":
        return f"{value.day} {month} {value.year}"
    if locale == "de":
        return f"{value.day}. {month} {value.year}"
    return f"{value.day} {month} {value.year}"


def fmt_datetime(value: datetime | None, locale: str) -> str:
    if value is None:
        return "—"
    return f"{fmt_date(value, locale)} · {fmt_time(value)}"


def fmt_today(locale: str, now: datetime) -> str:
    now = local(now)
    weekday = WEEKDAYS[locale][now.weekday()]
    weekday = weekday[0].upper() + weekday[1:]
    return f"{weekday}, {fmt_date(now, locale)}" if locale != "fr" else f"{weekday} {fmt_date(now, locale)}"


def fmt_money(cents: int | float | None, locale: str, currency: str = "EUR", decimals: bool | None = None) -> str:
    if cents is None:
        return "—"
    amount = cents / 100
    show_decimals = decimals if decimals is not None else (round(amount) != amount)
    number = f"{amount:,.2f}" if show_decimals else f"{amount:,.0f}"
    symbol = "€" if currency == "EUR" else currency
    if locale == "en":
        return f"{symbol}{number}"
    # fr / de: 1 234,50 € and 1.234,50 €
    sep = "\u202f" if locale == "fr" else "."
    number = number.replace(",", "§").replace(".", ",").replace("§", sep)
    return f"{number}\u00a0{symbol}"


def fmt_duration(minutes: float | int | None) -> str:
    if minutes is None:
        return "—"
    minutes = int(minutes)
    hours, rest = divmod(abs(minutes), 60)
    return f"{hours} h {rest:02d}" if hours else f"{rest} min"


def bay_short(zone_code: str | None, number: str | None) -> str:
    if not number:
        return "—"
    zone = "Ext." if zone_code == "EXT" else (zone_code or "").replace("L", "")
    return f"{zone} · {number}"


def fmt_input_datetime(value: datetime | None) -> str:
    """Value for an <input type="datetime-local"> in the site's time zone."""
    return local(value).strftime("%Y-%m-%dT%H:%M") if value else ""


def fmt_number(value: float | int | None, locale: str, digits: int = 1) -> str:
    """7.2 -> '7,2' in French and German, '7.2' in English; trailing ',0' is dropped."""
    if value is None:
        return "—"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return text if locale == "en" else text.replace(".", ",")
