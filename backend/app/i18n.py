"""Translations for the website and for messages returned to the Android app.

Texts live in app/locales/{fr,en,de}.json (flat "dot.keys"). The Android app ships
its own strings.xml but receives stable keys (message_key, reason, status) from the API,
so the same keys must exist on both sides.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import Request

from app.config import get_settings

LOCALES_DIR = Path(__file__).parent / "locales"
LOCALE_COOKIE = "pk_lang"


@lru_cache
def _catalog(locale: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def translate(key: str, locale: str, **params: object) -> str:
    settings = get_settings()
    text = _catalog(locale).get(key) or _catalog(settings.default_locale).get(key) or key
    if params:
        try:
            text = text.format(**params)
        except (KeyError, IndexError):
            pass
    return text


def negotiate_locale(request: Request, user_locale: str | None = None) -> str:
    """Order: ?lang= parameter, cookie, user preference, Accept-Language, default."""
    supported = get_settings().supported_locales
    for candidate in (request.query_params.get("lang"), request.cookies.get(LOCALE_COOKIE), user_locale):
        if candidate in supported:
            return candidate
    for part in request.headers.get("Accept-Language", "").split(","):
        code = part.split(";")[0].strip()[:2].lower()
        if code in supported:
            return code
    return get_settings().default_locale


class Translator:
    """Callable used in templates: {{ t('nav.dashboard') }}."""

    def __init__(self, locale: str):
        self.locale = locale

    def __call__(self, key: str, **params: object) -> str:
        return translate(key, self.locale, **params)
