"""Application settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://parking:parking@localhost:5432/parking"
        )
    )
    # Secret used to sign JWT access tokens. MUST be overridden in production.
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", "dev-only-change-me-not-for-production-use"))
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = field(default_factory=lambda: int(os.getenv("ACCESS_TOKEN_MINUTES", "30")))
    refresh_token_days: int = field(default_factory=lambda: int(os.getenv("REFRESH_TOKEN_DAYS", "30")))
    # Cookies used by the website (the Android app uses the Authorization header)
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o for o in os.getenv("CORS_ORIGINS", "").split(",") if o)
    )
    default_locale: str = field(default_factory=lambda: os.getenv("DEFAULT_LOCALE", "fr"))
    supported_locales: tuple[str, ...] = ("fr", "en", "de")
    # A controller is shown as offline after this many seconds without heartbeat
    controller_offline_after_s: int = field(default_factory=lambda: int(os.getenv("CONTROLLER_OFFLINE_AFTER_S", "120")))
    # Background job that turns unpaid sessions into payment alarms
    alarm_scan_interval_s: int = field(default_factory=lambda: int(os.getenv("ALARM_SCAN_INTERVAL_S", "60")))
    # --- Driver app and Bluetooth identification (schema v2) ---------------------------
    # Ed25519 key that signs the Bluetooth identity tokens of the phones (base64 of 32 random bytes).
    # When empty it is derived from JWT_SECRET, so a single-Pi prototype needs no extra secret.
    ble_signing_key: str = field(default_factory=lambda: os.getenv("BLE_SIGNING_KEY", ""))
    # Photos taken by agents before a fine (evidence). Must be a persistent volume in production.
    evidence_dir: Path = field(default_factory=lambda: Path(os.getenv("EVIDENCE_DIR", "./.evidence")))
    evidence_max_bytes: int = field(default_factory=lambda: int(os.getenv("EVIDENCE_MAX_BYTES", str(3 * 1024 * 1024))))
    # Return the SMS verification code in the API response (prototype without SMS gateway).
    # Never enable it on a public server.
    sms_debug: bool = field(default_factory=lambda: _bool("SMS_DEBUG", os.getenv("APP_ENV", "development") != "production"))

    # TEST ONLY: green "Turbo" button on the login page that signs in as the first administrator
    # without a password. Never enable it on a server reachable from outside the local network.
    turbo_login: bool = field(default_factory=lambda: _bool("TURBO_LOGIN", False))

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production and settings.jwt_secret == "dev-only-change-me-not-for-production-use":
        raise RuntimeError("JWT_SECRET must be set in production")
    return settings
