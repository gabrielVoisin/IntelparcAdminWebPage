-- Schema v5: multi-account platform.
--
-- organizations      the account that owns parkings: a commune, a canton or State, a property manager, an owner.
--                    Anyone can create one from the website ("Create an account"); its creator is its administrator.
--                    The bank details receive the monthly transfer of the rentals and bookings paid in the app.
-- staff_users        organization_id  the account the person belongs to (NULL only for platform staff)
--                    is_superuser     DR Automation: sees and may change or delete everything ("turbo" button)
-- sites              the "parking" object: organization_id (owner), capacity (number of places announced at creation),
--                    management_mode: how the places are watched
--                       qr_code          a QR code stuck on each place, no control device
--                       presence_sensor  one presence sensor per place (camera or ground sensor), each with its QR code
--                       smart_barrier    barriers at the entrance read the plate and open; each barrier has its QR code
-- devices            reference / qr_generated_at: QR code of a barrier ("IPK-XXXXXX", same alphabet as the bays)
-- customers          organization_id: the account that created this customer at the desk (drivers who registered in
--                    the app have none and are seen by the parkings where they booked, subscribed, parked or paid)
--
-- Existing data (single-site prototype) is attached to the internal account "DR Automation"; the oldest
-- administrator becomes the first super user.

DO $$ BEGIN
  CREATE TYPE organization_kind AS ENUM ('municipality', 'state', 'property_manager', 'owner', 'internal');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE management_mode AS ENUM ('qr_code', 'presence_sensor', 'smart_barrier');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS organizations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  kind            organization_kind NOT NULL DEFAULT 'owner',
  email           TEXT,
  phone           TEXT,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  bank_holder     TEXT,
  bank_iban       TEXT,                                -- stored without spaces, upper case
  bank_bic        TEXT,
  bank_address    TEXT,
  bank_updated_at TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS is_superuser BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS staff_users_org_idx ON staff_users (organization_id);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS management_mode management_mode NOT NULL DEFAULT 'presence_sensor';
ALTER TABLE sites ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 0 CHECK (capacity >= 0);
CREATE INDEX IF NOT EXISTS sites_org_idx ON sites (organization_id);

ALTER TABLE devices ADD COLUMN IF NOT EXISTS reference TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS qr_generated_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS devices_reference_key ON devices (reference);

ALTER TABLE customers ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

-- Attach rows created without an account (the prototype data, the demo seed) to the internal account.
-- Called by this migration and again after "python -m app.cli seed-demo".
CREATE OR REPLACE FUNCTION attach_orphans_to_internal() RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
  internal UUID;
BEGIN
  SELECT id INTO internal FROM organizations WHERE kind = 'internal' ORDER BY created_at LIMIT 1;
  IF internal IS NULL THEN
    INSERT INTO organizations (name, kind) VALUES ('DR Automation', 'internal') RETURNING id INTO internal;
  END IF;
  UPDATE sites SET organization_id = internal WHERE organization_id IS NULL;
  UPDATE staff_users SET organization_id = internal WHERE organization_id IS NULL;
  UPDATE sites s SET capacity = sub.n
    FROM (SELECT z.site_id, count(b.id) AS n FROM zones z JOIN bays b ON b.zone_id = z.id GROUP BY z.site_id) sub
   WHERE sub.site_id = s.id AND s.capacity = 0;
  IF NOT EXISTS (SELECT 1 FROM staff_users WHERE is_superuser) THEN
    UPDATE staff_users SET is_superuser = true
     WHERE id = (SELECT id FROM staff_users WHERE role = 'admin' AND is_active AND organization_id = internal
                 ORDER BY created_at LIMIT 1);
  END IF;
END
$fn$;

SELECT attach_orphans_to_internal();
