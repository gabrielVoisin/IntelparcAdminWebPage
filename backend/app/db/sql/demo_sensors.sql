-- Prototype inventory of the control devices: 3 cabinets x 100 car presence sensors.
-- Only CTRL-01 (the Raspberry Pi of the prototype) is installed, with one configured sensor, CP-001,
-- watching bay P1 / EXT / 05. Every other sensor is listed but not configured (no wiring address).
-- Safe to run again: python -m app.cli demo-sensors
DELETE FROM device_events;
DELETE FROM devices;

UPDATE controllers SET last_seen_at = NULL, agent_version = NULL, hardware = NULL, ip_address = NULL
WHERE code <> 'CTRL-01';

INSERT INTO devices (controller_id, code, kind, status)
SELECT c.id, 'CP-' || lpad(n::text, 3, '0'), 'front_presence_sensor', 'offline'
FROM controllers c CROSS JOIN generate_series(1, 100) AS n
WHERE c.code IN ('CTRL-01', 'CTRL-02', 'CTRL-03');

UPDATE devices d
SET address = 'gpio:1', label = 'Raspberry Pi 5 · caméra', status = 'ok', last_seen_at = now(),
    bay_id = (SELECT b.id FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id
              WHERE s.code = 'P1' AND z.code = 'EXT' AND b.number = '05')
FROM controllers c
WHERE c.id = d.controller_id AND c.code = 'CTRL-01' AND d.code = 'CP-001';
