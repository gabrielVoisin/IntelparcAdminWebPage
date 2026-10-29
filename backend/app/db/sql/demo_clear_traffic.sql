-- Prototype: remove every simulated car so the site only shows what the Raspberry Pi really detects
-- (one camera sensor, CP-001 on bay P1 / EXT / 05). The car parks themselves (sites, zones, bays, tariffs,
-- staff, customers and their subscriptions) are kept. Safe to run again: python -m app.cli clear-demo-traffic
DELETE FROM alarm_evidence;
DELETE FROM fines;
DELETE FROM payment_alarms;
DELETE FROM ble_identifications;
DELETE FROM parking_sessions;
DELETE FROM payments;
DELETE FROM reservations;
DELETE FROM bay_readings;
DELETE FROM device_events;

UPDATE bays SET is_occupied = false, occupied_since = NULL, admin_state = 'open', blocked_reason = NULL;
UPDATE subscriptions SET payment_overdue = false;
