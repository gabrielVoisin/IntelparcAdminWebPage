-- SQL checks of the driver app and the Bluetooth identification, on the demo data.
-- Runs with psql only (no Python needed) and rolls everything back:
--   psql "$DATABASE_URL_PSQL" -v ON_ERROR_STOP=1 -f backend/tests/sql/test_driver_sql.sql
-- Each check raises an exception when it fails.
\set QUIET on
BEGIN;

CREATE FUNCTION pg_temp.check(ok boolean, what text) RETURNS text LANGUAGE plpgsql AS $f$
BEGIN
  IF ok IS NOT TRUE THEN RAISE EXCEPTION 'check failed: %', what; END IF;
  RETURN 'ok  ' || what;
END $f$;
CREATE FUNCTION pg_temp.expect_error(stmt text, state text, what text) RETURNS text LANGUAGE plpgsql AS $f$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN others THEN
    IF SQLSTATE = state THEN RETURN 'ok  ' || what; END IF;
    RAISE EXCEPTION 'check failed: % (got % %)', what, SQLSTATE, SQLERRM;
  END;
  RAISE EXCEPTION 'check failed: % (no error)', what;
END $f$;

-- ---------------------------------------------------------------- fixtures
SELECT id AS site_p1 FROM sites WHERE code = 'P1' \gset
SELECT b.id AS bay12 FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id
 WHERE s.code = 'P1' AND z.code = 'EXT' AND b.number = '12' \gset
SELECT b.id AS bay03 FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id
 WHERE s.code = 'P1' AND z.code = 'EXT' AND b.number = '03' \gset
SELECT id AS camille FROM customers WHERE reference = 'C-00957' \gset
SELECT id AS lucas FROM customers WHERE reference = 'C-00412' \gset

-- ---------------------------------------------------------------- 1. self-registration
INSERT INTO customers (reference, first_name, last_name, email, password_hash)
VALUES ('C-' || nextval('customer_reference_seq'), 'Alex', 'Test', 'alex@example.com', 'scrypt$x')
RETURNING id AS alex, reference AS alex_ref \gset
SELECT pg_temp.check(:'alex_ref' LIKE 'C-1____', 'reference from the sequence: ' || :'alex_ref');
-- one app account per e-mail
SELECT pg_temp.expect_error($s$INSERT INTO customers (reference, first_name, last_name, email, password_hash) VALUES ('C-X', 'A', 'B', 'ALEX@example.com', 'h')$s$, '23505', 'one app account per e-mail');
INSERT INTO vehicles (customer_id, plate_display) VALUES (:'alex', 'GE 123 456') RETURNING id AS alex_car \gset

-- ---------------------------------------------------------------- 2. QR code lookup "P1/EXT/5" (bay "05")
SELECT count(*) AS found FROM v_bay_status v JOIN zones z ON z.id = v.zone_id JOIN sites s ON s.id = z.site_id
 WHERE upper(s.code) = 'P1' AND upper(z.code) = 'EXT' AND (v.number = '5' OR ltrim(v.number, '0') = ltrim('5', '0')) \gset
SELECT pg_temp.check(:found = 1, 'QR code P1/EXT/5 finds bay 05');

-- ---------------------------------------------------------------- 3. "right driver" decision (services/identification.decide)
CREATE TEMP TABLE decisions (who text, bay text, expected text, got text);
CREATE FUNCTION pg_temp.decide(c uuid, s uuid, b uuid) RETURNS text LANGUAGE plpgsql AS $f$
BEGIN
  IF EXISTS (SELECT 1 FROM v_customer_rights WHERE customer_id = c AND site_id = s AND bay_id = b) THEN RETURN 'authorised'; END IF;
  IF EXISTS (SELECT 1 FROM v_customer_rights WHERE bay_id = b AND customer_id <> c AND kind IN ('reservation', 'subscription')) THEN RETURN 'reserved_by_other'; END IF;
  IF EXISTS (SELECT 1 FROM v_customer_rights WHERE customer_id = c AND site_id = s AND kind = 'subscription') THEN RETURN 'authorised'; END IF;
  IF EXISTS (SELECT 1 FROM v_customer_rights WHERE customer_id = c AND site_id = s AND kind = 'reservation') THEN RETURN 'wrong_bay'; END IF;
  RETURN 'payment_required';
END $f$;
INSERT INTO decisions VALUES
  ('Camille (booked bay 12)', '12', 'authorised',        pg_temp.decide(:'camille', :'site_p1', :'bay12')),
  ('Lucas (subscriber)',      '12', 'reserved_by_other', pg_temp.decide(:'lucas',   :'site_p1', :'bay12')),
  ('Lucas (subscriber)',      '03', 'authorised',        pg_temp.decide(:'lucas',   :'site_p1', :'bay03')),
  ('Camille (booked bay 12)', '03', 'wrong_bay',         pg_temp.decide(:'camille', :'site_p1', :'bay03')),
  ('Alex (nothing paid)',     '03', 'payment_required',  pg_temp.decide(:'alex',    :'site_p1', :'bay03'));
SELECT who, bay, expected, got, CASE WHEN expected = got THEN 'ok' ELSE 'FAIL' END AS result FROM decisions;
SELECT pg_temp.check(NOT EXISTS (SELECT 1 FROM decisions WHERE expected <> got), 'right-driver decisions');

-- Alex pays 1 h in the app on bay 03 -> paid_session right on that bay only
INSERT INTO parking_sessions (site_id, bay_id, vehicle_id, customer_id, plate_display, entered_at, paid_until, identified_by, identified_at)
VALUES (:'site_p1', :'bay03', :'alex_car', :'alex', 'GE 123 456', now(), now() + interval '1 hour', 'app', now())
RETURNING id AS alex_session \gset
SELECT pg_temp.check(pg_temp.decide(:'alex', :'site_p1', :'bay03') = 'authorised', 'paid app session authorises its bay');
SELECT pg_temp.check(pg_temp.decide(:'alex', :'site_p1', :'bay12') = 'reserved_by_other', 'but not a booked bay');

-- ---------------------------------------------------------------- 4. free bay for a booking (app_driver.SQL_FREE_BAY_FOR_PERIOD)
SELECT b.id AS picked, b.number AS picked_number, z.code AS picked_zone FROM bays b JOIN zones z ON z.id = b.zone_id
WHERE z.site_id = :'site_p1' AND b.admin_state = 'open' AND b.kind <> 'accessible'
  AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.bay_id = b.id AND r.status IN ('confirmed', 'active')
                  AND tstzrange(r.starts_at, r.ends_at) && tstzrange(now() + interval '1 hour', now() + interval '3 hours'))
  AND NOT EXISTS (SELECT 1 FROM subscriptions su WHERE su.bay_id = b.id AND su.status = 'active'
                  AND current_date BETWEEN su.starts_on AND su.ends_on)
  AND NOT (b.is_occupied AND CAST(now() + interval '1 hour' AS timestamptz) < now() + interval '30 minutes')
ORDER BY z.level DESC, z.sort_order, b.number LIMIT 1 \gset
SELECT pg_temp.check(:'picked' <> :'bay12', 'free bay picked for a booking: ' || :'picked_zone' || ' ' || :'picked_number');
-- booking it twice on overlapping times is refused by the database itself
INSERT INTO reservations (customer_id, bay_id, starts_at, ends_at, channel, price_cents)
VALUES (:'alex', :'picked', now() + interval '1 hour', now() + interval '3 hours', 'app', 500);
SELECT pg_temp.expect_error(format($s$INSERT INTO reservations (customer_id, bay_id, starts_at, ends_at, channel) VALUES (%L, %L, now() + interval '2 hours', now() + interval '4 hours', 'app')$s$, :'lucas', :'picked'), '23P01', 'overlapping booking refused by the database');

-- ---------------------------------------------------------------- 5. Bluetooth identification waiting for its car
SELECT id AS ctrl FROM controllers WHERE code = 'CTRL-01' \gset
INSERT INTO ble_identifications (controller_id, identification_uid, customer_id, vehicle_id, rssi, identified_at)
VALUES (:'ctrl', 'ble-test-1', :'lucas', NULL, -61, now() - interval '2 minutes') RETURNING id AS ident \gset
-- same uid twice: refused (the Pi resends after a network failure)
SELECT pg_temp.expect_error(format($s$INSERT INTO ble_identifications (controller_id, identification_uid, customer_id, identified_at) VALUES (%L, 'ble-test-1', %L, now())$s$, :'ctrl', :'lucas'), '23505', 'identification resent by the Pi is ignored');
-- a car arrives on bay 12 one minute later: the waiting identification is found (identification.match_waiting_identification)
INSERT INTO parking_sessions (site_id, bay_id, entered_at) VALUES (:'site_p1', :'bay12', now() - interval '1 minute')
RETURNING id AS car12, entered_at AS car12_at \gset
SELECT i.id AS matched FROM ble_identifications i JOIN controllers c ON c.id = i.controller_id
WHERE c.site_id = :'site_p1' AND i.session_id IS NULL AND (i.bay_id = :'bay12' OR i.bay_id IS NULL)
  AND i.identified_at BETWEEN CAST(:'car12_at' AS timestamptz) - interval '10 minutes' AND CAST(:'car12_at' AS timestamptz) + interval '10 minutes'
ORDER BY (i.bay_id = :'bay12') DESC NULLS LAST, i.identified_at DESC LIMIT 1 \gset
SELECT pg_temp.check(:'matched' = :'ident', 'car detected after the phone: waiting identification found');
-- the session closest in time is found from the identification side as well
SELECT p.id AS closest FROM parking_sessions p
WHERE p.site_id = :'site_p1' AND p.status = 'open' AND p.customer_id IS NULL AND p.bay_id IS NOT NULL
  AND p.entered_at BETWEEN now() - interval '12 minutes' AND now() + interval '8 minutes'
ORDER BY abs(extract(epoch FROM p.entered_at - (now() - interval '2 minutes'))) LIMIT 1 \gset
SELECT pg_temp.check(:'closest' = :'car12', 'phone without bay: closest arriving car found');
-- Lucas (subscriber) on Camille's booked bay -> misuse alarm, raised once only
INSERT INTO payment_alarms (site_id, bay_id, session_id, reason, detected_at)
VALUES (:'site_p1', :'bay12', :'car12', 'reserved_bay_misuse', now()) ON CONFLICT DO NOTHING;
INSERT INTO payment_alarms (site_id, bay_id, session_id, reason, detected_at)
VALUES (:'site_p1', :'bay12', :'car12', 'reserved_bay_misuse', now()) ON CONFLICT DO NOTHING;
SELECT count(*) AS misuse FROM payment_alarms WHERE session_id = :'car12' \gset
SELECT pg_temp.check(:misuse = 1, 'reserved bay misuse alarm raised once');
UPDATE ble_identifications SET session_id = :'car12', bay_id = :'bay12', status = 'reserved_by_other' WHERE id = :'ident';

-- ---------------------------------------------------------------- 6. alarm list with "identified by" (queries.payment_alarms)
UPDATE parking_sessions SET customer_id = :'lucas', identified_by = 'bluetooth', identified_at = now() WHERE id = :'car12';
SELECT a.reason, ps.identified_by::text AS identified_by,
       CASE WHEN c.id IS NOT NULL THEN c.first_name || ' ' || left(c.last_name, 1) || '.' END AS customer_name,
       (SELECT count(*) FROM alarm_evidence e WHERE e.alarm_id = a.id) AS evidence_count
FROM payment_alarms a LEFT JOIN parking_sessions ps ON ps.id = a.session_id LEFT JOIN customers c ON c.id = ps.customer_id
WHERE a.session_id = :'car12';

-- ---------------------------------------------------------------- 7. map data (app_driver._sites, _tariff)
SELECT s.code, coalesce(o.free_bays, 0) AS free_bays,
       (SELECT amount_cents FROM tariffs WHERE kind = 'hourly' AND valid_to IS NULL AND (site_id = s.id OR site_id IS NULL)
        ORDER BY site_id NULLS LAST LIMIT 1) AS hourly_cents
FROM sites s LEFT JOIN v_site_occupancy o ON o.site_id = s.id WHERE s.is_active ORDER BY s.code;

-- the alarm engine does not flag Alex (paid in the app)
SELECT count(*) AS flagged FROM parking_sessions p LEFT JOIN reservations r ON r.id = p.reservation_id
WHERE p.id = :'alex_session' AND p.status = 'open' AND p.subscription_id IS NULL AND p.paid_until IS NULL \gset
SELECT pg_temp.check(:flagged = 0, 'app payment prevents the no_payment alarm');

\echo 'driver app SQL checks: all passed'
ROLLBACK;
