-- =====================================================================
-- Demo data matching the UI mockup. Never load this in production.
-- Staff password for every demo account: ChangeMe-2026!
-- Controller API keys (Raspberry Pi):
--   CTRL-01  ctrl0001.demo-secret-change-me-1
--   CTRL-02  ctrl0002.demo-secret-change-me-2
--   CTRL-03  ctrl0003.demo-secret-change-me-3
-- =====================================================================
BEGIN;

-- Sites ----------------------------------------------------------------
INSERT INTO sites (code, name, address_line, postcode, city, latitude, longitude) VALUES
  ('P1', 'Parking 1 · Centre',  '[Adresse]', '74100', 'Annemasse', 46.193600, 6.234000),
  ('P2', 'Parking 2 · Gare',    '[Adresse]', '74100', 'Annemasse', 46.190900, 6.241200),
  ('P3', 'Parking 3 · Rives',   '[Adresse]', '74100', 'Annemasse', 46.186100, 6.229700);
UPDATE sites SET is_active = true;

-- Zones ----------------------------------------------------------------
INSERT INTO zones (site_id, code, name, level, sort_order)
SELECT s.id, z.code, z.name, z.level, z.sort_order
FROM sites s
JOIN (VALUES
  ('P1', 'EXT', 'Extérieur',  0, 0), ('P1', 'L0', 'Niveau 0', 0, 1), ('P1', 'L-1', 'Niveau −1', -1, 2), ('P1', 'L-2', 'Niveau −2', -2, 3),
  ('P2', 'L0',  'Niveau 0',   0, 0), ('P2', 'L-1', 'Niveau −1', -1, 1),
  ('P3', 'EXT', 'Extérieur',  0, 0)
) AS z(site, code, name, level, sort_order) ON z.site = s.code;

-- Bays: 4 rows x 14 per zone (56), except P2 zones (90) and P3 (120 = 2 x 60)
INSERT INTO bays (zone_id, number, kind, plan_row, plan_col, photo_x, photo_y)
SELECT z.id,
       lpad(n::text, greatest(2, length(n::text)), '0'),
       CASE WHEN n <= 2 THEN 'accessible'::bay_kind WHEN n IN (27, 28) THEN 'ev'::bay_kind ELSE 'standard'::bay_kind END,
       ((n - 1) / 14) + 1,
       ((n - 1) % 14) + 1,
       round(((55 + ((n - 1) % 14) * 55) / 880.0 * 100)::numeric, 2),
       round(((ARRAY[16, 186, 276, 446])[((n - 1) / 14) % 4 + 1] / 552.0 * 100)::numeric, 2)
FROM zones z
JOIN sites s ON s.id = z.site_id
CROSS JOIN LATERAL generate_series(1, CASE WHEN s.code = 'P2' THEN 90 WHEN s.code = 'P3' THEN 120 ELSE 56 END) AS n;

-- Outdoor zone of P1: occupancy exactly as on the mockup
UPDATE bays b SET is_occupied = true, occupied_since = now() - ((b.number::int % 7) + 1) * interval '35 minutes'
FROM zones z JOIN sites s ON s.id = z.site_id
WHERE b.zone_id = z.id AND s.code = 'P1' AND z.code = 'EXT'
  AND b.number::int IN (1,2,4,5,6,8,9,11,12,13,15,17,18,19,20,21,24,25,27,28,30,31,32,34,35,36,37,38,40,41,42,43,44,45,47,49,50,51,52,55,56);

-- Other zones: ~60 % occupied
UPDATE bays b SET is_occupied = true, occupied_since = now() - interval '2 hours'
FROM zones z JOIN sites s ON s.id = z.site_id
WHERE b.zone_id = z.id AND NOT (s.code = 'P1' AND z.code = 'EXT') AND (b.number::int * 7 + z.sort_order * 11) % 10 < 6;
UPDATE bays b SET is_occupied = true FROM zones z JOIN sites s ON s.id = z.site_id
WHERE b.zone_id = z.id AND s.code = 'P3';                           -- P3 is full on the mockup

UPDATE bays b SET admin_state = 'blocked', blocked_reason = 'Travaux', is_occupied = false, occupied_since = NULL
FROM zones z JOIN sites s ON s.id = z.site_id
WHERE b.zone_id = z.id AND s.code = 'P1' AND z.code = 'L-2' AND b.number = '40';

-- Staff ----------------------------------------------------------------
INSERT INTO staff_users (email, full_name, password_hash, role, locale, last_login_at) VALUES
  ('responsable@example.com', 'Responsable de site', 'scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM=', 'admin',   'fr', now()),
  ('claire.dumont@example.com', 'Claire Dumont',     'scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM=', 'manager', 'fr', now() - interval '2 hours'),
  ('samir.ait@example.com',     'Samir Aït',         'scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM=', 'agent',   'fr', now() - interval '1 day'),
  ('laura.pittet@example.com',  'Laura Pittet',      'scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM=', 'agent',   'de', now() - interval '3 days'),
  ('compta@example.com',        'Comptabilité',      'scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM=', 'viewer',  'en', now() - interval '6 days');

INSERT INTO staff_site_access (staff_user_id, site_id)
SELECT u.id, s.id FROM staff_users u JOIN sites s ON
  (u.email = 'claire.dumont@example.com' AND s.code = 'P1') OR
  (u.email = 'samir.ait@example.com'     AND s.code IN ('P1', 'P2')) OR
  (u.email = 'laura.pittet@example.com'  AND s.code = 'P3');

-- Tariffs (current + history) -------------------------------------------
INSERT INTO tariffs (site_id, kind, amount_cents, valid_from, valid_to) VALUES
  (NULL, 'hourly',  220, '2025-01-01', '2026-03-15 09:30+01'),
  (NULL, 'hourly',  250, '2026-03-15 09:30+01', NULL),
  (NULL, 'daily',  1600, '2025-01-01', '2026-09-01 08:00+02'),
  (NULL, 'daily',  1800, '2026-09-01 08:00+02', NULL),
  (NULL, 'monthly',15000, '2025-01-01', '2026-09-01 08:00+02'),
  (NULL, 'monthly',16000, '2026-09-01 08:00+02', NULL);

INSERT INTO subscription_plans (code, name, price_cents, period_months, access_hours) VALUES
  ('MONTHLY_247',   '{"fr": "Mensuel 24/7", "en": "Monthly 24/7", "de": "Monatlich 24/7"}', 16000, 1, '24/7'),
  ('ANNUAL_247',    '{"fr": "Annuel 24/7", "en": "Annual 24/7", "de": "Jährlich 24/7"}', 14000, 12, '24/7'),
  ('DAYTIME',       '{"fr": "Jour (7h–19h)", "en": "Daytime (7am–7pm)", "de": "Tagsüber (7–19 Uhr)"}', 11000, 1, '07:00-19:00'),
  ('NIGHT_WEEKEND', '{"fr": "Nuit & week-end", "en": "Nights & weekends", "de": "Nachts & Wochenende"}', 8500, 1, 'nights_weekends'),
  ('BUSINESS',      '{"fr": "Entreprise", "en": "Business", "de": "Firmenkunde"}', 15000, 1, '24/7');

-- Customers and vehicles ------------------------------------------------
INSERT INTO customers (reference, first_name, last_name, email, phone, address_line, postcode, city, created_at) VALUES
  ('C-00412', 'Lucas',   'Bernard', 'lucas.bernard@example.com',  '+33600000412', '12 rue des Lilas',        '74100', 'Annemasse',               '2024-03-04'),
  ('C-00957', 'Camille', 'Martin',  'camille.martin@example.com', '+33600000957', '4 avenue du Lac',         '74200', 'Thonon-les-Bains',        '2025-06-11'),
  ('C-01033', 'Julien',  'Perret',  'julien.perret@example.com',  '+33600001033', '27 chemin des Vignes',    '01210', 'Ferney-Voltaire',         '2026-09-02'),
  ('C-00288', 'Hugo',    'Blanc',   'hugo.blanc@example.com',     '+33600000288', '3 place de la Gare',      '74160', 'Saint-Julien-en-Genevois','2023-01-09'),
  ('C-00671', 'Sophie',  'Lambert', 'sophie.lambert@example.com', '+33600000671', '18 rue du Marché',        '74000', 'Annecy',                  '2024-11-20'),
  ('C-00845', 'Thomas',  'Dubois',  'thomas.dubois@example.com',  '+33600000845', '9 boulevard Carnot',      '74100', 'Annemasse',               '2025-04-02');

INSERT INTO vehicles (customer_id, plate_display, make, model, color)
SELECT c.id, v.plate, v.make, v.model, v.color FROM customers c JOIN (VALUES
  ('C-00412', 'EF-456-GH',  'Peugeot',    '308',     'gris'),
  ('C-00957', 'GE-215 908', 'Renault',    'Clio',    'blanc'),
  ('C-01033', 'VD-90 214',  'Volkswagen', 'Golf',    'noir'),
  ('C-00288', 'JU-7 881',   'Toyota',     'Yaris',   'bleu'),
  ('C-00671', 'AB-908-ZX',  'Citroën',    'C3',      'rouge'),
  ('C-00845', 'NE-12 384',  'Dacia',      'Sandero', 'gris')
) AS v(ref, plate, make, model, color) ON v.ref = c.reference;

-- Subscriptions -----------------------------------------------------------
INSERT INTO subscriptions (customer_id, vehicle_id, site_id, bay_id, plan_id, price_cents, starts_on, ends_on, auto_renew, status, payment_overdue)
SELECT c.id, v.id, s.id, b.id, p.id, p.price_cents, x.starts_on::date, x.ends_on::date, x.auto, x.status::subscription_status, x.overdue
FROM (VALUES
  ('C-00412', 'P1', 'L0',  '07', 'MONTHLY_247',   '2026-10-01', '2026-10-31', true,  'active', false),
  ('C-00288', 'P1', 'L-1', '22', 'MONTHLY_247',   '2026-10-10', '2026-11-09', true,  'active', false),
  ('C-00671', 'P1', 'L-2', '31', 'NIGHT_WEEKEND', '2026-09-12', '2026-10-12', false, 'active', true)
) AS x(ref, site, zone, bay, plan, starts_on, ends_on, auto, status, overdue)
JOIN customers c ON c.reference = x.ref
JOIN vehicles v ON v.customer_id = c.id
JOIN sites s ON s.code = x.site
JOIN zones z ON z.site_id = s.id AND z.code = x.zone
JOIN bays b ON b.zone_id = z.id AND b.number = x.bay
JOIN subscription_plans p ON p.code = x.plan;

-- Reservations (relative to "now" so the demo always looks alive) ------
INSERT INTO reservations (customer_id, vehicle_id, bay_id, starts_at, ends_at, status, channel, price_cents)
SELECT c.id, v.id, b.id, now() + x.start_off, now() + x.end_off, x.status::reservation_status, x.channel::booking_channel, x.price
FROM (VALUES
  ('C-00957', 'P1', 'EXT', '12', interval '-2 hours',  interval '2 hours',   'active',    'web',   1000),
  ('C-00845', 'P1', 'L-1', '02', interval '-3 hours',  interval '-20 minutes','active',   'web',    750),
  ('C-00288', 'P1', 'L-1', '22', interval '4 hours',   interval '9 hours',   'confirmed', 'app',   1250),
  ('C-00412', 'P1', 'EXT', '14', interval '30 minutes',interval '3 hours',   'confirmed', 'phone',  625)
) AS x(ref, site, zone, bay, start_off, end_off, status, channel, price)
JOIN customers c ON c.reference = x.ref
JOIN vehicles v ON v.customer_id = c.id
JOIN sites s ON s.code = x.site
JOIN zones z ON z.site_id = s.id AND z.code = x.zone
JOIN bays b ON b.zone_id = z.id AND b.number = x.bay;

-- Open sessions and payment alarms on the outdoor zone of P1 -------------
INSERT INTO parking_sessions (site_id, bay_id, vehicle_id, plate_display, entered_at, paid_until)
SELECT s.id, b.id, v.id, x.plate, now() - x.since, x.paid
FROM (VALUES
  ('05', 'VD-90 214',  interval '50 minutes', NULL::timestamptz),
  ('36', 'AB-123-CD',  interval '2 hours',    now() - interval '45 minutes'),
  ('55', 'ZH-330 871', interval '35 minutes', NULL::timestamptz)
) AS x(bay, plate, since, paid)
JOIN sites s ON s.code = 'P1'
JOIN zones z ON z.site_id = s.id AND z.code = 'EXT'
JOIN bays b ON b.zone_id = z.id AND b.number = x.bay
LEFT JOIN vehicles v ON v.plate_norm = normalize_plate(x.plate);

INSERT INTO payment_alarms (site_id, bay_id, session_id, plate_display, reason, detail, detected_at)
SELECT ps.site_id, ps.bay_id, ps.id, ps.plate_display, x.reason::alarm_reason, x.detail, now() - x.ago
FROM (VALUES
  ('VD-90 214',  'no_payment',           NULL,  interval '35 minutes'),
  ('AB-123-CD',  'overstay',             '45',  interval '15 minutes'),
  ('ZH-330 871', 'plate_not_recognised', NULL,  interval '2 minutes')
) AS x(plate, reason, detail, ago)
JOIN parking_sessions ps ON ps.plate_norm = normalize_plate(x.plate);

INSERT INTO payments (site_id, customer_id, amount_cents, method, status, paid_at)
SELECT s.id, NULL, 250 + (g % 6) * 125, (ARRAY['card','contactless','app','card'])[g % 4 + 1]::payment_method, 'succeeded', now() - g * interval '9 minutes'
FROM sites s, generate_series(1, 60) g WHERE s.code = 'P1';

-- Controllers (Raspberry Pi) and devices ----------------------------------
INSERT INTO controllers (site_id, code, name, api_key_prefix, api_key_hash, hardware, agent_version, last_seen_at)
SELECT s.id, x.code, x.name, x.prefix, x.hash, 'Raspberry Pi 5', '1.0.0', now() - interval '5 seconds'
FROM (VALUES
  ('P1', 'CTRL-01', 'Armoire Parking 1', 'ctrl0001', '863b469ff63b9d6eec8ee7a21fc74084179e149ca7ffded1624bce5a74f2a131'),
  ('P2', 'CTRL-02', 'Armoire Parking 2', 'ctrl0002', 'c36bc9909f036a7f56f0701ed1c4fea40c6a3ffe754ac40eff43825c9be404e3'),
  ('P3', 'CTRL-03', 'Armoire Parking 3', 'ctrl0003', '64c58f1f8771a0f6985d130bc4b5ec36c9bb5ab0d8cea272ed6dd7b1778f7c2c')
) AS x(site, code, name, prefix, hash) JOIN sites s ON s.code = x.site;

-- One front + one vertical presence sensor per bay, and cameras
INSERT INTO devices (controller_id, bay_id, code, kind, address)
SELECT c.id, b.id, k.prefix || '-' || z.code || '-' || b.number, k.kind::device_kind, 'gpio:' || (row_number() OVER (PARTITION BY c.id, k.kind ORDER BY z.sort_order, b.number))
FROM controllers c
JOIN zones z ON z.site_id = c.site_id
JOIN bays b ON b.zone_id = z.id
CROSS JOIN (VALUES ('CPF', 'front_presence_sensor'), ('CPV', 'vertical_presence_sensor')) AS k(prefix, kind);

INSERT INTO devices (controller_id, code, kind, label, address)
SELECT c.id, x.code, 'camera', x.label, x.addr FROM controllers c
JOIN (VALUES
  ('CTRL-01', 'CAM-01', 'Entrée voie 1', 'rtsp://10.0.1.21/stream1'),
  ('CTRL-01', 'CAM-02', 'Sortie',        'rtsp://10.0.1.22/stream1'),
  ('CTRL-02', 'CAM-03', 'Sortie',        'rtsp://10.0.2.21/stream1'),
  ('CTRL-03', 'CAM-04', 'Entrée',        'rtsp://10.0.3.21/stream1')
) AS x(ctrl, code, label, addr) ON x.ctrl = c.code;

UPDATE devices d SET status = 'fault' FROM controllers c
WHERE c.id = d.controller_id AND (c.code, d.code) IN
  (('CTRL-01', 'CAM-01'), ('CTRL-01', 'CPF-EXT-22'), ('CTRL-02', 'CAM-03'), ('CTRL-02', 'CPV-L0-12'), ('CTRL-03', 'CPF-EXT-11'), ('CTRL-03', 'CPV-EXT-34'));

INSERT INTO device_events (controller_id, device_id, event_uid, code, priority, message_key, raised_at, cleared_at)
SELECT c.id, d.id, x.uid, x.code, x.prio::event_priority, x.msg, now() - x.ago, CASE WHEN x.cleared THEN now() - x.ago + interval '4 minutes' END
FROM (VALUES
  ('CTRL-01', 'e-0001', 'CAM-01',     'alarm',   'device.camera_faulty',          interval '80 minutes',  false),
  ('CTRL-01', 'e-0002', 'CPF-EXT-22', 'warning', 'device.front_sensor_faulty',    interval '3 hours',     false),
  ('CTRL-02', 'e-0001', 'CPV-L0-12',  'alarm',   'device.vertical_sensor_faulty', interval '45 minutes',  false),
  ('CTRL-02', 'e-0002', 'CAM-03',     'alarm',   'device.camera_faulty',          interval '42 minutes',  false),
  ('CTRL-02', 'e-0003', 'NET-SRV',    'warning', 'device.server_connection_lost', interval '6 minutes',   false),
  ('CTRL-03', 'e-0001', 'CPF-EXT-11', 'warning', 'device.front_sensor_faulty',    interval '96 minutes',  false),
  ('CTRL-03', 'e-0002', 'CPV-EXT-34', 'warning', 'device.vertical_sensor_faulty', interval '3 hours 40 minutes', false),
  ('CTRL-03', 'e-0003', 'NET-SRV',    'warning', 'device.server_connection_lost', interval '4 hours',     true)
) AS x(ctrl, uid, code, prio, msg, ago, cleared)
JOIN controllers c ON c.code = x.ctrl
LEFT JOIN devices d ON d.controller_id = c.id AND d.code = x.code;

-- Settings ------------------------------------------------------------------
INSERT INTO settings (site_id, key, value) VALUES
  (NULL, 'general.site_name',        '"[NOM DU PARKING]"'),
  (NULL, 'general.open_24h',         'true'),
  (NULL, 'general.currency',         '"EUR"'),
  (NULL, 'control.grace_alarm_min',  '15'),
  (NULL, 'control.grace_exit_min',   '15'),
  (NULL, 'control.overstay_min',     '10'),
  (NULL, 'control.fine_cents',       '3500'),
  (NULL, 'control.occupancy_alert_pct', '90'),
  (NULL, 'control.anpr_enabled',     'true'),
  (NULL, 'control.auto_fine',        'false'),
  (NULL, 'payments.billing_mode',    '"started_hour"'),
  (NULL, 'payments.free_minutes',    '15'),
  (NULL, 'payments.vat_bp',          '2000'),
  (NULL, 'payments.lost_ticket_cents','2500'),
  (NULL, 'payments.methods',         '["card", "contactless", "app", "invoice"]'),
  (NULL, 'security.session_minutes', '30'),
  (NULL, 'security.require_2fa',     'true'),
  (NULL, 'gdpr.plate_retention_days','30'),
  (NULL, 'gdpr.video_retention_days','7'),
  (NULL, 'gdpr.anonymise_exports',   'true');

INSERT INTO notification_rules (event, channel, enabled)
SELECT e, ch, (ch = 'email') OR (e IN ('payment_alarm', 'device_fault') AND ch = 'sms') OR (e IN ('payment_alarm', 'booking_overdue', 'site_full', 'device_fault') AND ch = 'app')
FROM unnest(ARRAY['payment_alarm', 'booking_overdue', 'site_full', 'device_fault', 'subscription_unpaid', 'weekly_report']) e
CROSS JOIN unnest(ARRAY['email', 'sms', 'app']) ch;

COMMIT;
