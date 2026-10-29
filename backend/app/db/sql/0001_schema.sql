-- =====================================================================
-- Parking management platform - database schema v1
-- PostgreSQL 16
--
-- Conventions (shared with the Android app and the Raspberry Pi agents):
--   * primary keys        : UUID (gen_random_uuid), except high-volume
--                           telemetry tables which use BIGINT identity
--   * names               : snake_case, plural table names
--   * timestamps          : TIMESTAMPTZ, stored and exchanged in UTC (ISO 8601)
--   * money               : INTEGER amount in cents + CHAR(3) ISO 4217 currency
--   * countries / locales : ISO 3166-1 alpha-2 / BCP 47 ("fr", "en", "de")
--   * licence plates      : plate_display as typed, plate_norm = upper-case,
--                           letters and digits only (used for every lookup)
--   * every table         : created_at / updated_at maintained by trigger
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------
CREATE TYPE staff_role          AS ENUM ('admin', 'manager', 'agent', 'viewer');
CREATE TYPE bay_kind            AS ENUM ('standard', 'accessible', 'ev');
CREATE TYPE bay_admin_state     AS ENUM ('open', 'blocked');
CREATE TYPE tariff_kind         AS ENUM ('hourly', 'daily', 'monthly');
CREATE TYPE billing_mode        AS ENUM ('started_hour', 'quarter_hour', 'minute');
CREATE TYPE reservation_status  AS ENUM ('confirmed', 'active', 'completed', 'cancelled', 'no_show');
CREATE TYPE booking_channel     AS ENUM ('web', 'app', 'phone', 'desk');
CREATE TYPE subscription_status AS ENUM ('active', 'suspended', 'terminated', 'expired');
CREATE TYPE session_status      AS ENUM ('open', 'closed');
CREATE TYPE payment_method      AS ENUM ('card', 'contactless', 'app', 'invoice', 'cash');
CREATE TYPE payment_status      AS ENUM ('pending', 'succeeded', 'failed', 'refunded');
CREATE TYPE alarm_reason        AS ENUM ('no_payment', 'overstay', 'plate_not_recognised', 'lost_ticket', 'accessible_bay_misuse');
CREATE TYPE alarm_status        AS ENUM ('open', 'acknowledged', 'fined');
CREATE TYPE fine_status         AS ENUM ('issued', 'paid', 'cancelled');
CREATE TYPE device_kind         AS ENUM ('camera', 'front_presence_sensor', 'vertical_presence_sensor', 'barrier', 'pay_terminal', 'display');
CREATE TYPE device_status       AS ENUM ('ok', 'fault', 'offline');
CREATE TYPE event_priority      AS ENUM ('alarm', 'warning', 'info');
CREATE TYPE report_format       AS ENUM ('pdf', 'xlsx');

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- Normalise a plate: "GE-215 908" -> "GE215908"
CREATE FUNCTION normalize_plate(p TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
  SELECT upper(regexp_replace(coalesce(p, ''), '[^A-Za-z0-9]', '', 'g'))
$$;

-- ---------------------------------------------------------------------
-- Sites, zones and bays
-- ---------------------------------------------------------------------
CREATE TABLE sites (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code          TEXT NOT NULL UNIQUE,                 -- "P1"
  name          TEXT NOT NULL,                        -- "Parking 1 · Centre"
  address_line  TEXT,
  postcode      TEXT,
  city          TEXT,
  country       CHAR(2) NOT NULL DEFAULT 'FR',
  timezone      TEXT NOT NULL DEFAULT 'Europe/Paris', -- IANA
  latitude      NUMERIC(9,6),
  longitude     NUMERIC(9,6),
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE zones (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id     UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  code        TEXT NOT NULL,                          -- "EXT", "L0", "L-1"
  name        TEXT NOT NULL,
  level       SMALLINT NOT NULL DEFAULT 0,            -- 0 = ground, -1 = basement 1
  sort_order  SMALLINT NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (site_id, code)
);

CREATE TABLE bays (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id         UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
  number          TEXT NOT NULL,                      -- "05"
  kind            bay_kind NOT NULL DEFAULT 'standard',
  admin_state     bay_admin_state NOT NULL DEFAULT 'open',
  blocked_reason  TEXT,
  -- live state, written by the presence sensors through the Pi agent
  is_occupied     BOOLEAN NOT NULL DEFAULT false,
  occupied_since  TIMESTAMPTZ,
  -- position on the plan (row / column) and on the aerial photo (% of width/height)
  plan_row        SMALLINT,
  plan_col        SMALLINT,
  photo_x         NUMERIC(5,2),
  photo_y         NUMERIC(5,2),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (zone_id, number)
);

-- ---------------------------------------------------------------------
-- Staff (back-office users) and access
-- ---------------------------------------------------------------------
CREATE TABLE staff_users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          TEXT NOT NULL,
  full_name      TEXT NOT NULL,
  password_hash  TEXT,                                -- NULL until the invitation is accepted
  role           staff_role NOT NULL DEFAULT 'viewer',
  locale         TEXT NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr', 'en', 'de')),
  is_active      BOOLEAN NOT NULL DEFAULT true,
  totp_secret    TEXT,                                -- two-factor authentication
  invited_at     TIMESTAMPTZ,
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX staff_users_email_uq ON staff_users (lower(email));

-- Sites a staff member can see. Admins see every site.
CREATE TABLE staff_site_access (
  staff_user_id  UUID NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
  site_id        UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  PRIMARY KEY (staff_user_id, site_id)
);

-- Refresh tokens for long-lived sessions (Android app, web)
CREATE TABLE refresh_tokens (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_user_id  UUID NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
  token_hash     TEXT NOT NULL UNIQUE,                -- SHA-256 of the token, never the token
  client_name    TEXT,                                -- "Android · Pixel 8"
  expires_at     TIMESTAMPTZ NOT NULL,
  revoked_at     TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Customers and vehicles
-- ---------------------------------------------------------------------
CREATE TABLE customers (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reference     TEXT NOT NULL UNIQUE,                 -- "C-00412"
  first_name    TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  email         TEXT,
  phone         TEXT,                                 -- E.164: +33600000412
  address_line  TEXT,
  postcode      TEXT,
  city          TEXT,
  country       CHAR(2) NOT NULL DEFAULT 'FR',
  locale        TEXT NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr', 'en', 'de')),
  company_name  TEXT,
  anonymised_at TIMESTAMPTZ,                          -- GDPR erasure
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX customers_name_idx ON customers (lower(last_name), lower(first_name));

CREATE TABLE vehicles (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id    UUID REFERENCES customers(id) ON DELETE SET NULL,
  plate_display  TEXT NOT NULL,
  plate_norm     TEXT GENERATED ALWAYS AS (normalize_plate(plate_display)) STORED,
  country        CHAR(2),
  make           TEXT,
  model          TEXT,
  color          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX vehicles_plate_uq ON vehicles (plate_norm);
CREATE INDEX vehicles_customer_idx ON vehicles (customer_id);

-- ---------------------------------------------------------------------
-- Pricing (one row per price version: the history is the table itself)
-- ---------------------------------------------------------------------
CREATE TABLE tariffs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id        UUID REFERENCES sites(id) ON DELETE CASCADE,   -- NULL = every site
  kind           tariff_kind NOT NULL,
  amount_cents   INTEGER NOT NULL CHECK (amount_cents >= 0),
  currency       CHAR(3) NOT NULL DEFAULT 'EUR',
  valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_to       TIMESTAMPTZ,                                   -- NULL = current price
  created_by     UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX tariffs_current_uq ON tariffs (coalesce(site_id, '00000000-0000-0000-0000-000000000000'::uuid), kind) WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------
-- Subscriptions and reservations
-- ---------------------------------------------------------------------
CREATE TABLE subscription_plans (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code          TEXT NOT NULL UNIQUE,                 -- "MONTHLY_247"
  name          JSONB NOT NULL,                       -- {"fr": "Mensuel 24/7", "en": "Monthly 24/7", "de": "Monatlich 24/7"}
  price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
  currency      CHAR(3) NOT NULL DEFAULT 'EUR',
  period_months SMALLINT NOT NULL DEFAULT 1,
  access_hours  TEXT,                                 -- "24/7", "07:00-19:00", "nights_weekends"
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id      UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  vehicle_id       UUID REFERENCES vehicles(id) ON DELETE SET NULL,
  site_id          UUID NOT NULL REFERENCES sites(id),
  bay_id           UUID REFERENCES bays(id) ON DELETE SET NULL,    -- assigned bay
  plan_id          UUID NOT NULL REFERENCES subscription_plans(id),
  price_cents      INTEGER NOT NULL CHECK (price_cents >= 0),      -- price at signature
  currency         CHAR(3) NOT NULL DEFAULT 'EUR',
  starts_on        DATE NOT NULL,
  ends_on          DATE NOT NULL,                                  -- current period end
  auto_renew       BOOLEAN NOT NULL DEFAULT true,
  status           subscription_status NOT NULL DEFAULT 'active',
  payment_overdue  BOOLEAN NOT NULL DEFAULT false,
  terminated_at    TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (ends_on >= starts_on)
);
CREATE INDEX subscriptions_customer_idx ON subscriptions (customer_id);
CREATE INDEX subscriptions_status_idx ON subscriptions (site_id, status, ends_on);

CREATE TABLE reservations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id   UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  vehicle_id    UUID REFERENCES vehicles(id) ON DELETE SET NULL,
  bay_id        UUID NOT NULL REFERENCES bays(id),
  starts_at     TIMESTAMPTZ NOT NULL,
  ends_at       TIMESTAMPTZ NOT NULL,
  status        reservation_status NOT NULL DEFAULT 'confirmed',
  channel       booking_channel NOT NULL DEFAULT 'web',
  price_cents   INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
  currency      CHAR(3) NOT NULL DEFAULT 'EUR',
  cancelled_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);
CREATE INDEX reservations_bay_time_idx ON reservations (bay_id, starts_at, ends_at);
CREATE INDEX reservations_status_idx ON reservations (status, ends_at);
-- A bay cannot be booked twice for overlapping times
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE reservations ADD CONSTRAINT reservations_no_overlap
  EXCLUDE USING gist (bay_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
  WHERE (status IN ('confirmed', 'active'));

-- ---------------------------------------------------------------------
-- Parking sessions (one per stay), payments, alarms, fines
-- ---------------------------------------------------------------------
CREATE TABLE parking_sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id          UUID NOT NULL REFERENCES sites(id),
  bay_id           UUID REFERENCES bays(id) ON DELETE SET NULL,
  vehicle_id       UUID REFERENCES vehicles(id) ON DELETE SET NULL,
  plate_display    TEXT,
  plate_norm       TEXT GENERATED ALWAYS AS (normalize_plate(plate_display)) STORED,
  reservation_id   UUID REFERENCES reservations(id) ON DELETE SET NULL,
  subscription_id  UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
  entered_at       TIMESTAMPTZ NOT NULL,
  exited_at        TIMESTAMPTZ,
  paid_until       TIMESTAMPTZ,
  status           session_status NOT NULL DEFAULT 'open',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (exited_at IS NULL OR exited_at >= entered_at)
);
CREATE INDEX parking_sessions_open_idx ON parking_sessions (site_id, status) WHERE status = 'open';
CREATE INDEX parking_sessions_plate_idx ON parking_sessions (plate_norm, entered_at DESC);

CREATE TABLE payments (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id          UUID REFERENCES sites(id),
  customer_id      UUID REFERENCES customers(id) ON DELETE SET NULL,
  session_id       UUID REFERENCES parking_sessions(id) ON DELETE SET NULL,
  reservation_id   UUID REFERENCES reservations(id) ON DELETE SET NULL,
  subscription_id  UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
  amount_cents     INTEGER NOT NULL,
  vat_rate_bp      INTEGER NOT NULL DEFAULT 2000,      -- basis points: 2000 = 20 %
  currency         CHAR(3) NOT NULL DEFAULT 'EUR',
  method           payment_method NOT NULL,
  status           payment_status NOT NULL DEFAULT 'pending',
  provider_ref     TEXT,                               -- payment provider id
  paid_at          TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payments_paid_idx ON payments (site_id, paid_at);

CREATE TABLE payment_alarms (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id          UUID NOT NULL REFERENCES sites(id),
  bay_id           UUID REFERENCES bays(id) ON DELETE SET NULL,
  session_id       UUID REFERENCES parking_sessions(id) ON DELETE SET NULL,
  plate_display    TEXT,
  reason           alarm_reason NOT NULL,
  detail           TEXT,                               -- e.g. overrun in minutes
  detected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  status           alarm_status NOT NULL DEFAULT 'open',
  handled_by       UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  handled_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payment_alarms_open_idx ON payment_alarms (site_id, detected_at DESC) WHERE status = 'open';
-- at most one open alarm per session and reason
CREATE UNIQUE INDEX payment_alarms_open_uq ON payment_alarms (session_id, reason) WHERE status = 'open';

CREATE TABLE fines (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alarm_id      UUID NOT NULL REFERENCES payment_alarms(id) ON DELETE CASCADE,
  amount_cents  INTEGER NOT NULL CHECK (amount_cents >= 0),
  currency      CHAR(3) NOT NULL DEFAULT 'EUR',
  status        fine_status NOT NULL DEFAULT 'issued',
  issued_by     UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_at       TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Control cabinets (one Raspberry Pi each) and their devices
-- ---------------------------------------------------------------------
CREATE TABLE controllers (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id           UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  code              TEXT NOT NULL UNIQUE,              -- "CTRL-01"
  name              TEXT NOT NULL,                     -- "Armoire Parking 1"
  api_key_prefix    TEXT NOT NULL UNIQUE,              -- first 8 chars, to find the key
  api_key_hash      TEXT NOT NULL,                     -- SHA-256 of the full key
  hardware          TEXT,                              -- "Raspberry Pi 5 8GB"
  agent_version     TEXT,
  ip_address        INET,
  last_seen_at      TIMESTAMPTZ,
  is_active         BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  controller_id  UUID NOT NULL REFERENCES controllers(id) ON DELETE CASCADE,
  bay_id         UUID REFERENCES bays(id) ON DELETE SET NULL,
  code           TEXT NOT NULL,                        -- "CPF-022", "CAM-01"
  kind           device_kind NOT NULL,
  label          TEXT,
  address        TEXT,                                 -- GPIO pin, Modbus id, camera URL...
  status         device_status NOT NULL DEFAULT 'ok',
  last_seen_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (controller_id, code)
);

-- Alarm / warning list shown on the "Control devices" screen
CREATE TABLE device_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  controller_id     UUID NOT NULL REFERENCES controllers(id) ON DELETE CASCADE,
  device_id         UUID REFERENCES devices(id) ON DELETE SET NULL,
  event_uid         TEXT NOT NULL,                     -- id generated by the Pi: idempotent upload
  code              TEXT NOT NULL,                     -- "CAM-01", "NET-SRV"
  priority          event_priority NOT NULL,
  message_key       TEXT NOT NULL,                     -- "device.camera_faulty" (translated in the UI and the app)
  raised_at         TIMESTAMPTZ NOT NULL,
  cleared_at        TIMESTAMPTZ,
  acknowledged_at   TIMESTAMPTZ,
  acknowledged_by   UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (controller_id, event_uid)
);
CREATE INDEX device_events_active_idx ON device_events (controller_id, raised_at DESC) WHERE cleared_at IS NULL;

-- Raw telemetry from the presence sensors and cameras (high volume)
CREATE TABLE bay_readings (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bay_id        UUID NOT NULL REFERENCES bays(id) ON DELETE CASCADE,
  device_id     UUID REFERENCES devices(id) ON DELETE SET NULL,
  reading_uid   TEXT NOT NULL,                         -- idempotency key from the Pi
  is_occupied   BOOLEAN NOT NULL,
  plate_display TEXT,                                  -- when a camera read a plate
  confidence    NUMERIC(4,3),
  measured_at   TIMESTAMPTZ NOT NULL,
  received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (bay_id, reading_uid)
);
CREATE INDEX bay_readings_time_idx ON bay_readings (bay_id, measured_at DESC);

-- ---------------------------------------------------------------------
-- Settings, notifications, reports, audit
-- ---------------------------------------------------------------------
CREATE TABLE settings (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id     UUID REFERENCES sites(id) ON DELETE CASCADE,   -- NULL = global
  key         TEXT NOT NULL,                                  -- "control.grace_alarm_min"
  value       JSONB NOT NULL,
  updated_by  UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX settings_key_uq ON settings (coalesce(site_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

CREATE TABLE notification_rules (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event       TEXT NOT NULL,                                  -- "payment_alarm", "device_fault"...
  channel     TEXT NOT NULL CHECK (channel IN ('email', 'sms', 'app')),
  enabled     BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (event, channel)
);

-- Push tokens for the Android app (Firebase Cloud Messaging)
CREATE TABLE push_devices (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_user_id  UUID NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
  platform       TEXT NOT NULL DEFAULT 'android' CHECK (platform IN ('android', 'ios', 'web')),
  token          TEXT NOT NULL UNIQUE,
  last_used_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE report_exports (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_type   TEXT NOT NULL,                                  -- "revenue", "transactions"...
  site_id       UUID REFERENCES sites(id) ON DELETE SET NULL,   -- NULL = every site
  period_from   DATE NOT NULL,
  period_to     DATE NOT NULL,
  format        report_format NOT NULL,
  file_size     INTEGER,
  created_by    UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id     UUID REFERENCES staff_users(id) ON DELETE SET NULL,
  actor_kind   TEXT NOT NULL DEFAULT 'staff' CHECK (actor_kind IN ('staff', 'controller', 'system')),
  action       TEXT NOT NULL,                                   -- "alarm.acknowledge"
  entity       TEXT NOT NULL,                                   -- "payment_alarms"
  entity_id    TEXT,
  data         JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity, entity_id);

-- ---------------------------------------------------------------------
-- updated_at triggers on every table that has the column
-- ---------------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
  FOR t IN
    SELECT table_name FROM information_schema.columns
    WHERE table_schema = 'public' AND column_name = 'updated_at'
  LOOP
    EXECUTE format('CREATE TRIGGER %I_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- Views used by the dashboard, the bay map and the Android app
-- ---------------------------------------------------------------------
CREATE VIEW v_site_occupancy AS
SELECT s.id AS site_id, s.code, s.name,
       count(b.id)                                                   AS total_bays,
       count(b.id) FILTER (WHERE b.is_occupied)                      AS occupied_bays,
       count(b.id) FILTER (WHERE NOT b.is_occupied AND b.admin_state = 'open') AS free_bays,
       count(b.id) FILTER (WHERE b.admin_state = 'blocked')          AS blocked_bays
FROM sites s
JOIN zones z ON z.site_id = s.id
JOIN bays b  ON b.zone_id = z.id
GROUP BY s.id;

CREATE VIEW v_bay_status AS
SELECT b.id AS bay_id, z.site_id, z.id AS zone_id, z.code AS zone_code, b.number, b.kind,
       CASE
         WHEN b.admin_state = 'blocked' THEN 'blocked'
         WHEN EXISTS (SELECT 1 FROM payment_alarms a WHERE a.bay_id = b.id AND a.status = 'open') THEN 'alarm'
         WHEN b.is_occupied THEN 'occupied'
         WHEN EXISTS (SELECT 1 FROM reservations r WHERE r.bay_id = b.id AND r.status IN ('confirmed', 'active')
                       AND r.starts_at <= now() + interval '2 hours' AND r.ends_at > now()) THEN 'reserved'
         ELSE 'free'
       END AS status,
       b.is_occupied, b.occupied_since, b.plan_row, b.plan_col, b.photo_x, b.photo_y
FROM bays b
JOIN zones z ON z.id = b.zone_id;
