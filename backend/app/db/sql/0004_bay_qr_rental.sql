-- QR code of each bay (admin tab "Generate QR code") and rental status of the bay.
--
-- reference          short public reference printed and encoded in the QR code ("IPK-7F3K9Q"); it links the
--                    sticker to this row of the main database, even if the bay is renumbered later
-- street / lat / lng where the bay is (street and number, GPS); the site's address and position are used when empty
-- rental_status      not_rented        the bay is not rented: anyone can book it in the app
--                    rented_sublet     the bay is rented but the tenant allows sub-letting: it can still be booked
--                    rented_exclusive  the bay is rented outright: nobody else can book it
DO $$ BEGIN
  CREATE TYPE bay_rental_status AS ENUM ('not_rented', 'rented_sublet', 'rented_exclusive');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE bays ADD COLUMN IF NOT EXISTS rental_status bay_rental_status NOT NULL DEFAULT 'not_rented';
ALTER TABLE bays ADD COLUMN IF NOT EXISTS reference TEXT;
ALTER TABLE bays ADD COLUMN IF NOT EXISTS street TEXT;
ALTER TABLE bays ADD COLUMN IF NOT EXISTS latitude NUMERIC(9,6);
ALTER TABLE bays ADD COLUMN IF NOT EXISTS longitude NUMERIC(9,6);
ALTER TABLE bays ADD COLUMN IF NOT EXISTS qr_generated_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS bays_reference_key ON bays (reference);
